"""Export publication figures from authorized, cached patient-held-out fields.

No training or source modification. Never exports CT, patient identifiers, UIDs,
absolute coordinates or voxel arrays. The CT-like backdrop is a mathematical
phantom, not patient anatomy; it cannot establish anatomical dose relationships.
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metrics import dose_metrics, local_target_metrics
from model import regularize_prediction


def choose_slice(labels):
    """Axial slice: most distinct targets, then target area, then lower index."""
    return max(range(labels.shape[0]), key=lambda z: (
        len(np.unique(labels[z][labels[z] > 0])), int((labels[z] > 0).sum()), -z))


def synthetic_ct(extent, shape):
    """Fixed analytic head phantom in relative mm; no source CT or anatomy input."""
    x = np.linspace(extent[0], extent[1], shape[1])
    y = np.linspace(extent[2], extent[3], shape[0])
    xx, yy = np.meshgrid(x, y)
    r = np.sqrt((xx / 90)**2 + (yy / 105)**2)
    hu = np.full(shape, -1000., dtype=float)
    hu[r < 1.06] = -60
    hu[r < 1.02] = 750
    hu[r < .96] = 28
    hu[r < .91] = 36
    vent = (((xx - 9) / 7)**2 + ((yy + 2) / 24)**2 < 1) | (
        ((xx + 9) / 7)**2 + ((yy + 2) / 24)**2 < 1)
    hu[vent] = 8
    return gaussian_filter(hu, 1.5)


def metric_rows(pred, ref, labels, domain, rx, spacing):
    rows, _ = local_target_metrics(pred, ref, labels, domain,
                                  [rx] * int(labels.max()), spacing)
    out = []
    for k, local in enumerate(rows, 1):
        mask = labels == k
        row = dict(target=f'T{k:02}', volume_cc=float(mask.sum()*spacing**3/1000),
                   local_coverage_pct=local['local_coverage_pct'])
        for kind, dose in [('reference', ref), ('predicted', pred)]:
            row.update({f'{kind}_{key}': value for key, value in local[kind].items()})
            d = dose_metrics(dose[mask], rx)
            row[f'{kind}_D98_Gy'] = d['D98_Gy'] if d else None
        out.append(row)
    return out


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(source_root, out, count=2):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    from matplotlib.lines import Line2D
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.hashsalt': 'dose-atlas-comparison-v1'})
    candidates = []
    for path in sorted((source_root/'private').glob('C??.npz')):
        with np.load(path) as a:
            labels = a['labels']
        z = choose_slice(labels)
        visible = len(np.unique(labels[z][labels[z] > 0]))
        candidates.append((visible, int(labels.max()), int((labels[z] > 0).sum()), path, z))
    candidates.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3].name))
    calibration = json.loads((source_root/'artifacts/dose_calibration.json').read_text())
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, (visible, total, area, path, z) in enumerate(candidates[:count]):
        example = f'Example {chr(65+index)}'
        slug = f'example-{chr(97+index)}'
        cid = path.stem
        meta = json.loads(path.with_suffix('.json').read_text())
        rx, spacing = meta['rx_Gy'], meta['spacing_mm']
        heldout = path.with_name(cid+'_heldout.npz')
        with np.load(path) as a:
            labels = a['labels']
            ref = a['y'].reshape(labels.shape)*rx
            domain = (a['X'][:, 0] <= 35).reshape(labels.shape)
        with np.load(heldout) as a:
            raw = a['pred'].reshape(labels.shape)
        scale = calibration['fold_scales'][cid]
        pred = regularize_prediction(raw, spacing)*rx*scale
        rows = metric_rows(pred, ref, labels, domain, rx, spacing)
        with (out/f'{slug}-metrics.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

        # Only translate the plotting origin; never deform the dose or targets.
        center = np.array(np.where(labels > 0)).mean(axis=1)
        extent = [(-.5-center[2])*spacing, (labels.shape[2]-.5-center[2])*spacing,
                  (-.5-center[1])*spacing, (labels.shape[1]-.5-center[1])*spacing]
        xs = (np.arange(labels.shape[2])-center[2])*spacing
        ys = (np.arange(labels.shape[1])-center[1])*spacing
        background_extent = [min(-100,extent[0]), max(100,extent[1]),
                             min(-120,extent[2]), max(120,extent[3])]
        phantom = synthetic_ct(background_extent, (300, 260))
        vmin, vmax = 0, max(30., np.ceil(max(float(np.nanmax(ref[z])), float(np.nanmax(pred[z])))/5)*5)
        norm = Normalize(vmin, vmax)
        fig, axes = plt.subplots(1, 3, figsize=(17, 7.7), gridspec_kw={'width_ratios': [1,1,1]})
        fig.patch.set_facecolor('#f4f7fa')
        fig.suptitle(f'{example}  |  {total} targets, {visible} intersect this axial slice',
                     x=.055, y=.98, ha='left', fontsize=21, fontweight='bold', color='#17364b')
        fig.text(.055, .92, f'DCA · single-isocenter · C-arm / TrueBeam · Elements 4.5   |   {rx:g} Gy / 1 fraction',
                 fontsize=12, color='#37566b')
        fields = [ref[z], pred[z], pred[z]-ref[z]]
        titles = ['Reference plan dose', 'Patient-held-out estimate', 'Estimate − reference']
        ims = []
        for j, (ax, field, title) in enumerate(zip(axes, fields, titles)):
            ax.set_facecolor('#091a27')
            ax.imshow(phantom, origin='lower', extent=background_extent, cmap='gray', vmin=-50, vmax=100)
            valid = domain[z] & np.isfinite(ref[z]) & np.isfinite(pred[z])
            mask = valid & ((np.maximum(ref[z], pred[z]) >= 3) if j == 2 else (field >= 3))
            im = ax.imshow(np.ma.masked_where(~mask, field), origin='lower', extent=extent,
                           cmap='RdBu_r' if j == 2 else 'turbo',
                           norm=Normalize(-5, 5) if j == 2 else norm, alpha=.85)
            ims.append(im)
            for k in np.unique(labels[z]):
                if not k: continue
                m = labels[z] == k
                ax.contour(xs, ys, m.astype(float), levels=[.5], colors='white', linewidths=1.1)
                y, x = np.array(np.where(m)).mean(axis=1)
                ax.annotate(f'T{int(k):02}', ((x-center[2])*spacing, (y-center[1])*spacing),
                            xytext=(5,6), textcoords='offset points', color='white', fontsize=9,
                            bbox=dict(facecolor='#102d40', alpha=.85, edgecolor='none', pad=2))
            ax.set_title(title, fontsize=15, pad=12, color='#17364b')
            ax.set_xlabel('Relative left–right position [mm]')
            if j == 0: ax.set_ylabel('Relative anterior–posterior position [mm]')
            else: ax.set_yticks([])
            ax.set_aspect('equal')
            ax.set_xlim(background_extent[:2]); ax.set_ylim(background_extent[2:])
            cb = fig.colorbar(im, ax=ax, orientation='horizontal', pad=.13, fraction=.05,
                             extend='both' if j == 2 else 'neither')
            cb.set_label('Difference [Gy] · saturated at ±5' if j == 2 else 'Dose [Gy] · overlay ≥3 Gy')
        fig.subplots_adjust(left=.055, right=.985, top=.85, bottom=.19, wspace=.12)
        fig.text(.055, .10, 'White contours = actual targets. Identical slice, grid and dose scale; no projection or target repositioning.', fontsize=11)
        fig.text(.055, .06, 'Background: synthetic CT-like phantom with blurred HU-like intensities — NOT patient CT or anatomical registration.', fontsize=11, color='#7a471b')
        fig.text(.055, .022, f'Estimate: held-out HGB + 1-mm Gaussian + training-fold calibration ×{scale:.2f}. Development example; not independent validation.', fontsize=10)
        fig.savefig(out/f'{slug}-slice.png', dpi=160)
        fig.savefig(out/f'{slug}-slice.svg', metadata={'Date': None})
        plt.close(fig)

        fig, axes = plt.subplots(1, 4, figsize=(17, 7.5), sharey=True)
        fig.suptitle(f'{example}  |  Whole-volume metrics for all {total} targets', x=.055, y=.98,
                     ha='left', fontsize=21, fontweight='bold', color='#17364b')
        fig.text(.055, .915, 'Pairs compare the original plan and patient-held-out estimate. These are 3D metrics, not measurements of the displayed slice.', fontsize=11)
        keys = [('local_Paddick_CI', 'Local Paddick CI [0–1]'), ('local_GI', 'Local GI'),
                ('local_V12_cc', 'Local V12 outside targets [cm³]'), ('D98_Gy', 'Target D98 [Gy] · focused axis')]
        yy = np.arange(total)
        for ax, (key, title) in zip(axes, keys):
            a = np.array([r[f'reference_{key}'] if r[f'reference_{key}'] is not None else np.nan for r in rows])
            b = np.array([r[f'predicted_{key}'] if r[f'predicted_{key}'] is not None else np.nan for r in rows])
            for y, va, vb in zip(yy, a, b): ax.plot([va, vb], [y,y], color='#9eacb5', lw=1.4)
            ax.scatter(a, yy, marker='o', color='#155c82', label='Reference plan', s=48, zorder=3)
            ax.scatter(b, yy, marker='D', facecolors='white', edgecolors='#b16a14', label='Held-out estimate', s=45, zorder=4)
            ax.set_title(title, fontsize=12, pad=15)
            ax.grid(axis='x', color='#e1e6ea', linewidth=.7); ax.set_axisbelow(True)
            ax.set_yticks(yy, [r['target'] for r in rows]); ax.set_ylim(total-.5, -.5)
            ax.set_xlim(left=0, right=1.03 if key == 'local_Paddick_CI' else None)
            if key == 'D98_Gy':
                ax.set_xlim(np.floor(min(np.nanmin(a),np.nanmin(b))-.5),
                            np.ceil(max(np.nanmax(a),np.nanmax(b))+.5))
                ax.axvline(rx, color='#888', ls=':', lw=1)
            good = np.isfinite(a)&np.isfinite(b)
            mae = np.mean(abs(a[good]-b[good])) if good.any() else np.nan
            ax.set_xlabel(f'Target MAE {mae:.3f}  |  n={int(good.sum())}')
        handles = [Line2D([],[],marker='o',ls='',color='#155c82',label='Reference plan'),
                   Line2D([],[],marker='D',ls='',markerfacecolor='white',color='#b16a14',label='Held-out estimate')]
        fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(.5,.13), ncol=2, frameon=False)
        fig.subplots_adjust(left=.055,right=.98,top=.82,bottom=.27,wspace=.3)
        fig.text(.055,.08,'CI, GI and V12: common nearest-target territories within the 35-mm domain. V12 excludes every selected target; no whole-brain claim.',fontsize=10)
        fig.text(.055,.035,'Missing support is reported as unavailable, never zero. CI optimism and target-specific errors remain; no clinical pass/fail judgement.',fontsize=10)
        fig.savefig(out/f'{slug}-metrics.png',dpi=160)
        fig.savefig(out/f'{slug}-metrics.svg',metadata={'Date':None})
        plt.close(fig)
        report = dict(example=example, targets=total, targets_in_slice=visible, rx_Gy=rx,
                      fractions=meta['fractions'], grid_mm=spacing, fold_calibration_scale=scale,
                      method='patient-held-out HGB, Gaussian sigma 1 mm, nested training-fold scalar',
                      background='fixed synthetic CT-like phantom; no patient CT; not anatomical registration',
                      selection='descending distinct targets in one axial slice, total targets, slice target area; not selected by accuracy',
                      private_input_sha256={'cache':sha(path),'heldout_prediction':sha(heldout)},
                      metrics=rows)
        (out/f'{slug}.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        reports.append({k:v for k,v in report.items() if k not in ['metrics','private_input_sha256']})
        print(json.dumps(reports[-1]),flush=True)
    (out/'index.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    # Matplotlib path lines carry trailing spaces; normalize only generated SVGs.
    for svg in out.glob('example-*.svg'):
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--count',type=int,default=2,choices=range(1,11))
    args=parser.parse_args()
    render(args.source_root,args.output,args.count)
