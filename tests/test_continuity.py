import numpy as np
import pytest
from model import regularize_prediction, scale_prescriptions
from geometry import InputError


def test_regularization_changes_field_preserves_bounds_and_input():
    raw = np.zeros((21,21,21), np.float32)
    raw[8:13,8:13,8:13] = 2
    before = raw.copy()
    out = regularize_prediction(raw, 1)
    np.testing.assert_array_equal(raw, before)
    assert out.min() >= 0 and out.max() <= 2
    assert np.any((out > 0) & (out < 2))
    assert len(np.unique(out[8:13,8:13,8:13])) > 1
    np.testing.assert_allclose(out.sum(), raw.sum(), rtol=1e-6)
    np.testing.assert_array_equal(regularize_prediction(np.ones_like(raw), 1), np.ones_like(raw))


def test_physical_sigma_and_per_prescription_linearity():
    rng = np.random.default_rng(9)
    raw = rng.uniform(0,2,(15,15,15)).astype(np.float32)
    np.testing.assert_array_equal(regularize_prediction(raw, .5, sigma_mm=.25),
                                  regularize_prediction(raw, 1, sigma_mm=.5))
    reg = regularize_prediction(raw, 1)
    labels = np.zeros(raw.shape, np.int16)
    labels[3:5,3:5,3:5] = 1
    labels[10:12,10:12,10:12] = 2
    a = scale_prescriptions(reg, labels, [18,22], 1)
    b = scale_prescriptions(reg, labels, [36,44], 1)
    np.testing.assert_allclose(b, 2*a, rtol=1e-6)
    np.testing.assert_allclose(a[labels==1], reg[labels==1]*18)
    np.testing.assert_allclose(a[labels==2], reg[labels==2]*22)


def test_invalid_regularization_rejected():
    with pytest.raises(InputError): regularize_prediction(np.ones((2,2)),1)
    with pytest.raises(InputError): regularize_prediction(np.full((2,2,2),np.nan),1)
    with pytest.raises(InputError): regularize_prediction(np.ones((2,2,2)),0)


def test_raw_mode_preserves_original_voxels_and_default_is_one_mm():
    from model import REGULARIZATION_SIGMA_MM
    a = np.random.default_rng(71).random((7,7,7),dtype=np.float32)
    raw = regularize_prediction(a,1,sigma_mm=0)
    assert REGULARIZATION_SIGMA_MM == 1
    np.testing.assert_array_equal(raw,a)
    assert not np.shares_memory(raw,a)
    np.testing.assert_array_equal(regularize_prediction(a,1),regularize_prediction(a,1,sigma_mm=1))


@pytest.mark.parametrize("calibrated", [False, True])
def test_compute_metrics_dvh_view_and_dicom_use_regularized_field(monkeypatch, calibrated):
    from io import BytesIO
    import pydicom
    import app as api
    from test_api import structure_bytes
    from geometry import structure_rois, make_grid
    from model import geometry_features
    from metrics import dose_metrics, dvh

    data, source = structure_bytes()
    rois = structure_rois(source)
    grid = make_grid(rois, [1])
    features, labels, _ = geometry_features(rois, [1], grid)
    def tree_prediction(model, x):
        return np.where(x[:,0] < 0, 1.2, .5).astype(np.float32)
    raw = tree_prediction(None, features).reshape(grid.shape)
    import dose_calibration
    monkeypatch.setattr(dose_calibration, "calibration_card", lambda: {"scale":1.04})
    expected = regularize_prediction(raw, grid.spacing)*(1.04 if calibrated else 1.)*20
    assert not np.array_equal(expected, raw*20)
    monkeypatch.setattr(api, 'load_model', lambda: None)
    monkeypatch.setattr(api, 'predict', tree_prediction)
    result, bundle = api.compute(data, None, '[1]', 20, 1, with_bundle=True, calibrated=calibrated)
    assert result['summary']['prescription_Gy'] == 20
    assert result['summary']['prediction_calibration_scale'] == (1.04 if calibrated else 1.)
    expected_metrics=dose_metrics(expected[labels==1],20)
    assert {key:result['metrics'][0]['predicted'][key] for key in expected_metrics} == expected_metrics
    assert result['dvhs'][0]['predicted'] == dvh(expected[labels==1],20)
    exported = pydicom.dcmread(BytesIO(bundle['prediction.dcm']))
    assert ('Empirical cohort dose calibration' in exported.DerivationDescription) == calibrated
    from storage import compact_result
    assert compact_result(result)['summary']['prediction_calibration_scale'] == (1.04 if calibrated else 1.)
    decoded = exported.pixel_array.astype(float)*float(exported.DoseGridScaling)
    np.testing.assert_allclose(decoded,expected,rtol=0,atol=float(exported.DoseGridScaling)/2+1e-10)
    view = result['slices']
    stride = round(view['spacing_mm']/grid.spacing)
    shown = np.asarray(view['predicted'],dtype=float)
    sampled = expected[np.asarray(view['indices']),::stride,::stride]
    mask = np.isfinite(shown)
    np.testing.assert_allclose(shown[mask],sampled[mask],atol=.00051,rtol=0)


def test_calibration_rejects_incompatible_raw_mode_and_bad_model(monkeypatch):
    from dose_calibration import calibration_factor
    with pytest.raises(InputError):calibration_factor(True,sigma_mm=0)
    with pytest.raises(InputError):calibration_factor("true")
    assert calibration_factor(False,sigma_mm=0) == 1
