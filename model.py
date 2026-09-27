"""Geometry-only dose surrogate; never uses observed dose at inference."""
import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter
from sklearn.ensemble import HistGradientBoostingRegressor
from geometry import InputError, rasterize

FEATURE_NAMES = ['signed_target_distance_mm', 'nearest_radius_mm', 'distance_over_radius',
                 'second_sphere_distance_mm', 'radial_overlap', 'target_count',
                 'total_target_cc', 'inside_depth_fraction']

REGULARIZATION_SIGMA_MM = 1.0


def regularize_prediction(relative, spacing, *, sigma_mm=REGULARIZATION_SIGMA_MM):
    """Spatial Gaussian regularization of the normalized 3-D prediction only.

    Fixed physical sigma, reflect boundary, four-sigma truncation. This changes
    the actual field, not the DVH; use its result for every downstream output.
    Apply before prescription scaling, never to an observed reference dose.
    It reduces tree plateaus but cannot guarantee unique voxel doses. The
    positive normalized kernel preserves bounds and constant fields. No
    target-dose renormalization or reference-dose fitting is performed.
    """
    a = np.asarray(relative)
    if a.ndim != 3 or not np.issubdtype(a.dtype, np.floating) or not np.isfinite(a).all():
        raise InputError('Regularisierung benötigt ein endliches dreidimensionales Dosisfeld.')
    if not np.isscalar(spacing) or not np.isfinite(spacing) or spacing <= 0:
        raise InputError('Rasterabstand muss positiv und endlich sein.')
    if not np.isfinite(sigma_mm) or sigma_mm < 0:
        raise InputError('Regularisierungsbreite muss endlich und nicht negativ sein.')
    if sigma_mm == 0:
        return a.copy()
    return gaussian_filter(a, sigma=float(sigma_mm)/float(spacing),
                           mode='reflect', truncate=4.0, output=np.float32)


def geometry_features(rois, targets, grid):
    labels = np.zeros(grid.shape, np.int16)
    stats = []
    for i, number in enumerate(targets, 1):
        mask = rasterize(rois[number], grid)
        count = int(mask.sum())
        if count < 4:
            raise InputError('Ein Ziel umfasst weniger als 4 Rastervoxel; feinere Geometrie erforderlich.')
        if np.any(labels[mask]):
            raise InputError('Überlappende Zielstrukturen: Sammelstruktur oder Einzelziele auswählen, nicht beides.')
        labels[mask] = i
        idx = np.array(np.where(mask)).mean(axis=1)[::-1]
        volume = count*grid.voxel_cc
        stats.append(dict(number=number, volume_cc=volume,
                          radius_mm=(3*volume*1000/(4*np.pi))**(1/3),
                          center=(idx*grid.spacing+grid.origin).tolist()))
    union = labels > 0
    distance, nearest = distance_transform_edt(~union, sampling=grid.spacing, return_indices=True)
    nearest_label = labels[tuple(nearest)]
    del nearest
    # Half-voxel boundary approximation, explicitly tested and disclosed.
    inside = distance_transform_edt(union, sampling=grid.spacing)
    signed = np.where(union, -(inside-grid.spacing/2), distance-grid.spacing/2).astype(np.float32)
    del distance, inside
    radii = np.array([0]+[s['radius_mm'] for s in stats], np.float32)
    radius = radii[nearest_label].ravel()
    X = np.empty((union.size, len(FEATURE_NAMES)), np.float32)
    X[:,0] = signed.ravel()
    X[:,1] = radius
    X[:,2] = X[:,0]/np.maximum(radius, .5)
    X[:,5] = len(targets)
    X[:,6] = sum(s['volume_cc'] for s in stats)
    X[:,7] = np.maximum(-X[:,0],0)/np.maximum(radius,.5)
    for start in range(0, len(X), 150_000):
        stop = min(start+150_000, len(X))
        xyz = grid.points(start, stop)
        sphere_d = np.stack([np.linalg.norm(xyz-s['center'], axis=1)-s['radius_mm'] for s in stats])
        if len(targets) > 1:
            X[start:stop,3] = np.partition(sphere_d, 1, axis=0)[1]
        else:
            X[start:stop,3] = 100
        X[start:stop,4] = np.exp(-np.maximum(sphere_d,0)/8).sum(axis=0)
    return X, labels, stats


def baseline(X):
    return np.where(X[:,0] < 0, 1 + .18*np.minimum(X[:,7],1),
                    np.exp(-np.maximum(X[:,0],0)/(2.5+.35*X[:,1])))


def new_regressor():
    # Fixed before LOPO; early stopping disabled (no random voxel validation).
    return HistGradientBoostingRegressor(max_iter=90, max_leaf_nodes=15,
        learning_rate=.075, l2_regularization=10, min_samples_leaf=80,
        early_stopping=False, random_state=20260920)


def predict(model, X):
    result = np.empty(len(X), np.float32)
    for start in range(0, len(X), 200_000):
        stop = min(start+200_000,len(X))
        result[start:stop] = np.clip(model.predict(X[start:stop]), 0, 2)
    return result


def sample_training(X, dose_relative, seed, per_band=4000):
    rng = np.random.default_rng(seed)
    y = dose_relative.ravel()
    d = X[:,0]
    finite = np.isfinite(y)
    bands = [(d < 0), ((d >= 0)&(d < 5)), ((d >= 5)&(d < 15)), ((d >= 15)&(d <= 35))]
    selected = []
    for band in bands:
        indices = np.flatnonzero(band & finite)
        if not len(indices):
            raise InputError('Keine Trainingsvoxel in einer erforderlichen Distanzzone.')
        selected.append(rng.choice(indices, per_band, replace=len(indices)<per_band))
    idx = np.concatenate(selected)
    return X[idx], y[idx]


def prescription_values(targets, common_rx, overrides=None):
    """Validate explicit technical limits; learned range is disclosed separately."""
    if type(common_rx) not in (int,float) or not np.isfinite(common_rx) or not 0 < common_rx <= 100:
        raise InputError('Verschreibung muss größer als 0 und höchstens 100 Gy sein.')
    if overrides is None: overrides={}
    if not isinstance(overrides,dict) or any(k not in {str(n) for n in targets} for k in overrides):
        raise InputError('Target-Verschreibungen müssen ausgewählten ROI-Nummern zugeordnet sein.')
    if any(type(v) not in (int,float) or not np.isfinite(v) or not 0 < v <= 100 for v in overrides.values()):
        raise InputError('Target-Verschreibungen müssen größer als 0 und höchstens 100 Gy sein.')
    return [float(overrides.get(str(n),common_rx)) for n in targets]


def scale_prescriptions(relative, labels, values, spacing):
    """Exploratory heterogeneous Rx interpolation, without smoothing model dose.

    The selected normalized prediction is multiplied by a scale field. Target voxels
    use their own prescription; outside, inverse squared EDT weights blend Rx.
    Uniform values take exactly the historical scalar multiplication path.
    """
    if not values: raise InputError('Mindestens ein Ziel erforderlich.')
    if all(v==values[0] for v in values):return relative*values[0]
    weighted=np.zeros(labels.shape,np.float32)
    weights=np.zeros(labels.shape,np.float32)
    for k,rx in enumerate(values,1):
        distance=distance_transform_edt(labels!=k,sampling=spacing)
        np.maximum(distance,spacing/2,out=distance)
        np.square(distance,out=distance)
        np.reciprocal(distance,out=distance)
        weights+=distance
        distance*=rx
        weighted+=distance
        del distance
    np.divide(weighted,weights,out=weighted)
    scale=weighted
    del weights
    for k,rx in enumerate(values,1):scale[labels==k]=rx
    return relative*scale
