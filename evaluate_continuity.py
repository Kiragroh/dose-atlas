"""Development-cohort spatial regularization audit of cached LOPO predictions.

No training, hyperparameter search, reference correction, or DICOM loading.
Only deidentified case/target numbers and aggregate errors are written.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from model import regularize_prediction, REGULARIZATION_SIGMA_MM


def plateau_pct(values):
    _, counts = np.unique(values, return_counts=True)
    return float(100*counts.max()/len(values))


def concentration_pct(values, width_Gy=.05):
    """Maximum mass in any closed width-Gy interval, independent of bin origin."""
    a = np.sort(np.asarray(values, dtype=float))
    return float(100*np.max(np.searchsorted(a,a+width_Gy,side='right')-np.arange(len(a)))/len(a))


def evaluate(root):
    folds = []
    for path in sorted((root/'private').glob('C??_heldout.npz')):
        cid = path.stem.split('_')[0]
        meta = json.loads((root/'private'/f'{cid}.json').read_text())
        with np.load(root/'private'/f'{cid}.npz') as source:
            labels, y = source['labels'], source['y']
            distance = source['X'][:, 0].copy()
        with np.load(path) as source:
            raw = source['pred'].reshape(labels.shape)
        spacing, rx = meta['spacing_mm'], meta['rx_Gy']
        reg = regularize_prediction(raw, spacing)
        y = y.reshape(labels.shape)
        distance = distance.reshape(labels.shape)
        supported = np.isfinite(y) & (distance <= 35)
        target_rows = []
        for k in range(1, int(labels.max())+1):
            mask = labels == k
            ref_d98 = float(np.percentile(y[mask], 2)*rx)
            target_rows.append(dict(target=k, reference_D98_Gy=ref_d98,
                raw_D98_Gy=float(np.percentile(raw[mask], 2)*rx),
                regularized_D98_Gy=float(np.percentile(reg[mask], 2)*rx),
                raw_max_plateau_pct=plateau_pct(raw[mask]),
                regularized_max_plateau_pct=plateau_pct(reg[mask]),
                raw_max_005Gy_interval_pct=concentration_pct(raw[mask]*rx),
                regularized_max_005Gy_interval_pct=concentration_pct(reg[mask]*rx),
                reference_max_005Gy_interval_pct=concentration_pct(y[mask]*rx)))
        bands = {}
        for name, band in [('target', distance<0), ('0_5mm', (distance>=0)&(distance<5)),
                           ('5_15mm', (distance>=5)&(distance<15)),
                           ('15_35mm', (distance>=15)&(distance<=35))]:
            m = supported & band
            bands[name] = {key: float(np.mean(np.abs(pred[m]-y[m]))*rx)
                           for key, pred in [('raw_MAE_Gy',raw),('regularized_MAE_Gy',reg)]}
        outside = supported & (labels == 0)
        volumes = {key: float(np.count_nonzero(pred[outside]*rx >= 12)*spacing**3/1000)
                   for key,pred in [('raw',raw),('regularized',reg),('reference',y)]}
        folds.append(dict(case=cid, bands=bands, targets=target_rows,
                          V12_outside_targets_domain_cc=volumes))
        print(cid, 'evaluated', flush=True)
    if len(folds) != 10:
        raise ValueError('This audit requires all ten cached LOPO folds.')
    aggregate = dict(bands={band: {key:float(np.mean([f['bands'][band][key] for f in folds]))
                                 for key in folds[0]['bands'][band]} for band in folds[0]['bands']})
    for mode in ('raw','regularized'):
        aggregate[mode+'_D98_case_macro_MAE_Gy'] = float(np.mean([
            np.mean([abs(t[mode+'_D98_Gy']-t['reference_D98_Gy']) for t in f['targets']]) for f in folds]))
        aggregate[mode+'_V12_outside_case_MAE_cc'] = float(np.mean([
            abs(f['V12_outside_targets_domain_cc'][mode]-f['V12_outside_targets_domain_cc']['reference']) for f in folds]))
        plateaus = [t[mode+'_max_plateau_pct'] for f in folds for t in f['targets']]
        aggregate[mode+'_max_plateau_target_mean_pct'] = float(np.mean(plateaus))
        aggregate[mode+'_max_plateau_target_worst_pct'] = float(max(plateaus))
    for mode in ('raw','regularized','reference'):
        masses = [t[mode+'_max_005Gy_interval_pct'] for f in folds for t in f['targets']]
        aggregate[mode+'_max_005Gy_interval_target_mean_pct'] = float(np.mean(masses))
        aggregate[mode+'_max_005Gy_interval_target_worst_pct'] = float(max(masses))
    d98_changes = [abs(t['regularized_D98_Gy']-t['reference_D98_Gy'])-
                   abs(t['raw_D98_Gy']-t['reference_D98_Gy']) for f in folds for t in f['targets']]
    v12_changes = [abs(f['V12_outside_targets_domain_cc']['regularized']-
                       f['V12_outside_targets_domain_cc']['reference'])-
                   abs(f['V12_outside_targets_domain_cc']['raw']-
                       f['V12_outside_targets_domain_cc']['reference']) for f in folds]
    aggregate.update(target_count=len(d98_changes),
                     D98_error_worsened_target_count=sum(v>0 for v in d98_changes),
                     D98_largest_error_increase_Gy=float(max(d98_changes)),
                     V12_error_worsened_case_count=sum(v>0 for v in v12_changes),
                     V12_largest_error_increase_cc=float(max(v12_changes)),
                     target_voxel_MAE_worsened_case_count=sum(f['bands']['target']['regularized_MAE_Gy']>f['bands']['target']['raw_MAE_Gy'] for f in folds))
    return dict(method='1.0-mm sigma Gaussian applied to cached patient-held-out predictions; development choice after visual inspection and comparison of 0.5/1.0 mm on this cohort, not independent validation',
                sigma_mm=REGULARIZATION_SIGMA_MM, fwhm_mm=float(np.sqrt(8*np.log(2))*REGULARIZATION_SIGMA_MM),
                boundary='reflect', truncate_sigma=4, cases=len(folds),
                limitations='Exploratory same development cohort; no independent validation; no claim of optimal or deliverable dose.',
                aggregate=aggregate, folds=folds)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'private'/'continuity_audit.json')
    args = parser.parse_args()
    report = evaluate(Path(__file__).resolve().parent)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    public = {key: value for key,value in report.items() if key != 'folds'}
    (Path(__file__).parent/'artifacts'/'continuity_validation.json').write_text(
        json.dumps(public, indent=2), encoding='utf-8')
    print(json.dumps(report['aggregate'], indent=2))
