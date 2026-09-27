"""Common-grid descriptive comparison. No clinical pass/fail scoring."""
import numpy as np
from scipy.ndimage import distance_transform_edt, find_objects


def ring_auc(values, rx, voxel_cc):
    """Exact integral of empirical cumulative volume over relative dose [0.5, 0.8]."""
    a = np.asarray(values, dtype=np.float64)
    if not len(a) or not np.isfinite(a).all() or rx <= 0:
        return None
    return float(np.clip(a / rx - .5, 0, .3).sum() * voxel_cc)


def ring_benchmark(pred, ref, labels, domain, rx, spacing, names, width_mm=10, prescriptions=None):
    """Exploratory geometric ring comparison, not a validated brain-sparing score.

    Rings exclude all selected targets, may overlap each other, and are never
    pooled. Distance follows the half-voxel boundary convention in model.py.
    Reject incomplete support rather than changing the ring between plans.
    """
    rows=[]
    margin=int(np.ceil(width_mm/spacing))+2
    voxel_cc=spacing**3/1000
    for k, bounds in enumerate(find_objects(labels), 1):
        if bounds is None: continue
        crop=tuple(slice(max(0,b.start-margin),min(n,b.stop+margin)) for b,n in zip(bounds,labels.shape))
        local=labels[crop]
        target=local==k
        target_rx=prescriptions[k-1] if prescriptions is not None else rx
        distance=distance_transform_edt(~target,sampling=spacing)-spacing/2
        ring=(local==0)&(distance<=width_mm)
        count=int(ring.sum())
        clipped=any((s.start==0 and np.any(np.take(ring,0,axis=a))) or
                    (s.stop==labels.shape[a] and np.any(np.take(ring,-1,axis=a)))
                    for a,s in enumerate(crop))
        supported=domain[crop]&np.isfinite(pred[crop])
        if ref is not None: supported &= np.isfinite(ref[crop])
        coverage=100*int((ring&supported).sum())/count if count else 0
        complete=count>0 and not clipped and bool(np.all(supported[ring]))
        pa=ring_auc(pred[crop][ring],target_rx,voxel_cc) if complete else None
        ra=ring_auc(ref[crop][ring],target_rx,voxel_cc) if complete and ref is not None else None
        reason=None
        if not count: reason='Leerer geometrischer Ring.'
        elif clipped: reason='Ring berührt den Rasterrand; vollständiges Ringvolumen nicht gesichert.'
        elif not complete: reason='Ring nicht vollständig durch beide Dosisfelder unterstützt.'
        elif ref is None: reason='Keine Referenzdosis hochgeladen.'
        elif ra <= 1e-12: reason='Referenz-AUC ist null; Quotient nicht definiert.'
        pm=dose_metrics(pred[crop][target],target_rx)
        rm=dose_metrics(ref[crop][target],target_rx) if ref is not None else None
        rows.append(dict(roi=names[k-1],prescription_Gy=target_rx,ring_volume_cc=count*voxel_cc,coverage_pct=coverage,
                         predicted_auc_cc=pa,reference_auc_cc=ra,
                         ratio=pa/ra if reason is None else None,reason=reason,
                         predicted_D98_Gy=pm['D98_Gy'] if pm else None,
                         reference_D98_Gy=rm['D98_Gy'] if rm else None,
                         reference_V100_pct=rm['V100_pct'] if rm else None))
    return dict(ring_width_mm=width_mm,relative_dose_interval=[.5,.8],
                definition='10-mm-Außenring je Target, alle ausgewählten Targets ausgeschlossen; ohne Gehirnmaske. Ringe können überlappen und werden nicht summiert.',
                rows=rows)


def dose_metrics(values, rx):
    a = np.asarray(values)
    if len(a) == 0 or not np.isfinite(a).all():
        return None
    return dict(D98_Gy=float(np.percentile(a,2)), D95_Gy=float(np.percentile(a,5)),
                Dmean_Gy=float(a.mean()), D2_Gy=float(np.percentile(a,98)),
                V100_pct=float(100*np.mean(a>=rx)))


def dvh(values, rx):
    """Empirical cumulative DVH with duplicate-dose points for vertical steps.

    At each retained dose, emit V(D>=dose) and its right limit V(D>dose).
    Large curves retain at most 201 dose knots selected by voxel rank; their
    horizontal approximation misses at most ceil((N-1)/200)/N of the volume.
    Metrics still use every original voxel, never this display representation.
    """
    a = np.asarray(values, dtype=np.float64).ravel()
    if not len(a) or not np.isfinite(a).all():
        return None
    a = np.sort(a)
    knots = np.unique(a)
    if len(knots) > 201:
        ranks = np.rint(np.linspace(0, len(a)-1, 201)).astype(int)
        knots = np.unique(a[ranks])
    before = 100*(len(a)-np.searchsorted(a,knots,side='left'))/len(a)
    after = 100*(len(a)-np.searchsorted(a,knots,side='right'))/len(a)
    points = [[min(0.,float(a[0])),100.]]
    for dose, upper, lower in zip(knots,before,after):
        points.extend([[float(dose),round(float(upper),6)],
                       [float(dose),round(float(lower),6)]])
    points.append([max(2*float(rx),float(a[-1])*1.01),0.])
    return points


def global_metrics(dose, labels, domain, rx, voxel_cc, mixed_prescriptions=False):
    supported = domain & np.isfinite(dose)
    targets = labels > 0
    if not np.all(supported[targets]):
        return None
    tv = int(targets.sum())
    piv = int(((dose>=rx)&supported).sum())
    half = int(((dose>=rx*.5)&supported).sum())
    overlap = int(((dose>=rx)&targets).sum())
    return dict(target_volume_cc=tv*voxel_cc,
                Paddick_CI=overlap**2/(tv*piv) if piv and not mixed_prescriptions else None,
                GI=half/piv if piv and not mixed_prescriptions else None,
                V12_domain_cc=float(((dose>=12)&supported).sum()*voxel_cc),
                V12_outside_targets_domain_cc=float(((dose>=12)&supported&~targets).sum()*voxel_cc))


def local_target_metrics(pred, ref, labels, domain, prescriptions, spacing):
    """Partition the 35-mm domain by nearest target-mask voxel (physical EDT).

    One common, dose-independent territory per target; EDT deterministically
    assigns equidistant voxels. These are domain-limited local indices, not
    independent single-lesion plans or full-brain measurements. Incomplete
    support invalidates BOTH plans' local indices, never shrinking territories.
    """
    nearest = distance_transform_edt(labels == 0, sampling=spacing,
                                     return_distances=False, return_indices=True)
    owner = labels[tuple(nearest)]
    del nearest
    voxel_cc = spacing**3/1000
    rows = []
    for k, rx in enumerate(prescriptions, 1):
        territory = (owner == k) & domain
        target = labels == k
        count = int(territory.sum())
        support = np.isfinite(pred) & domain
        if ref is not None:
            support &= np.isfinite(ref)
        complete = count > 0 and bool(np.all(support[territory])) and bool(np.all(territory[target]))
        coverage = 100*int((territory & support).sum())/count if count else 0.
        row = {'local_coverage_pct': coverage, 'local_region_cc': count*voxel_cc}
        for kind, dose in (('predicted', pred), ('reference', ref)):
            if dose is None:
                row[kind] = None
                continue
            values = dict(local_Paddick_CI=None, local_inverse_CI=None,
                          local_GI=None, local_V12_cc=None)
            if complete:
                tv = int(target.sum())
                piv = int(((dose >= rx) & territory).sum())
                half = int(((dose >= rx*.5) & territory).sum())
                overlap = int(((dose >= rx) & target).sum())
                ci = overlap**2/(tv*piv) if tv and piv else None
                values.update(local_Paddick_CI=ci,
                              local_inverse_CI=1/ci if ci else None,
                              local_GI=half/piv if piv else None,
                              local_V12_cc=float(((dose >= 12) & territory & (labels == 0)).sum()*voxel_cc))
            row[kind] = values
        rows.append(row)
    means = {}
    for kind in ('predicted', 'reference'):
        if kind == 'reference' and ref is None:
            means[kind] = None
            continue
        means[kind] = {}
        for source, dest in (('local_inverse_CI', 'mean_local_inverse_CI'),
                             ('local_Paddick_CI', 'mean_local_CI'), ('local_GI', 'mean_local_GI')):
            values = [r[kind][source] for r in rows if r[kind] is not None and r[kind][source] is not None
                      and (ref is None or (r['predicted'][source] is not None and r['reference'][source] is not None))]
            means[kind][dest] = float(np.mean(values)) if values else None
            means[kind][dest+'_n'] = len(values)
    return rows, means
