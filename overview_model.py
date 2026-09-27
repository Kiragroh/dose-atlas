"""Separate exploratory distal-dose extension; original model is unchanged.

Train only on known RTDOSE samples from the original ten-case private caches.
This is a geometry surrogate, not physical calculation or learned OAR sparing.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '4')
import hashlib
import json
from pathlib import Path
import joblib
import numpy as np
from model import new_regressor, predict

VERSION = 'overview-distal-hgb-v1'
BLEND_MM = (30., 45.)


def predict_overview(near_model, distal_model, features):
    result = predict(near_model, features)
    selected = features[:, 0] > BLEND_MM[0]
    if selected.any():
        weight = np.clip((features[selected, 0]-BLEND_MM[0])/(BLEND_MM[1]-BLEND_MM[0]), 0, 1)
        weight = weight*weight*(3-2*weight)
        result[selected] = (1-weight)*result[selected]+weight*predict(distal_model, features[selected])
    return result


def distal_samples(features, dose_relative, seed, per_band=4000):
    rng = np.random.default_rng(seed)
    distance = features[:, 0]
    valid = np.isfinite(dose_relative) & (dose_relative >= 0)
    indices = []
    for low, high in [(25, 35), (35, 60), (60, 100), (100, np.inf)]:
        options = np.flatnonzero(valid & (distance >= low) & (distance < high))
        if len(options):
            indices.extend(rng.choice(options, min(per_band, len(options)), replace=False))
    if not indices:
        raise ValueError('No supported distal-dose training samples')
    return features[indices].copy(), dose_relative[indices].copy()


def train(root):
    root = Path(root)
    samples, hashes = [], {}
    for index, path in enumerate(sorted((root/'private').glob('C[0-9][0-9].npz'))):
        with np.load(path) as data:
            samples.append(distal_samples(data['X'], data['y'], 20260924+index))
        hashes[path.stem] = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(samples) < 3:
        raise ValueError('At least three independent original cases required')
    folds = []
    for i, (test_x, test_y) in enumerate(samples):
        train_x = np.concatenate([x for j, (x, _) in enumerate(samples) if j != i])
        train_y = np.concatenate([y for j, (_, y) in enumerate(samples) if j != i])
        model = new_regressor().fit(train_x, train_y)
        selected = test_x[:, 0] >= BLEND_MM[1]
        folds.append({'case': i+1, 'held_out_case': True,
                      'relative_dose_MAE_beyond_45mm': float(np.mean(abs(predict(model, test_x[selected])-test_y[selected]))) if selected.any() else None,
                      'samples_beyond_45mm': int(selected.sum())})
        print('distal held-out case', i+1, 'complete', flush=True)
    all_x = np.concatenate([x for x, _ in samples]); all_y = np.concatenate([y for _, y in samples])
    model = new_regressor().fit(all_x, all_y)
    output = root/'artifacts'/'overview'; output.mkdir(exist_ok=True)
    joblib.dump(model, output/'distal.joblib')
    card = {'version': VERSION, 'training_cases': len(samples),
            'training_sources': 'Existing original ten-case caches; no new register case or outcome data added. Register/training overlap is not an independent validation cohort.',
            'cache_sha256': hashes, 'blend_mm': list(BLEND_MM),
            'sampled_distance_range_mm': [float(all_x[:, 0].min()), float(all_x[:, 0].max())],
            'validation': {'method': 'leave-one-original-case-out, distal model only, sampled voxels', 'folds': folds},
            'research_only': True, 'clinical_validation': False,
            'limitations': ['No independent full-organ DVH validation.',
                'Near model retained through 30 mm; blended with distal surrogate through 45 mm.',
                'No learned OAR sparing, beam geometry, patient density, or physical transport.',
                'Distance or case geometry outside training support is unvalidated extrapolation.',
                'Cannot interpret these values as clinical organ tolerance or RN probability.']}
    (output/'model_card.json').write_text(json.dumps(card, indent=2), encoding='utf-8')
    print('distal overview model ready;', len(samples), 'cases; max sampled distance', round(float(all_x[:, 0].max()), 1), 'mm', flush=True)


if __name__ == '__main__':
    train(Path(__file__).resolve().parent)
