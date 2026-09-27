import numpy as np
from scripts.render_plan_comparison import choose_slice, metric_rows, synthetic_ct


def test_slice_selection_prefers_count_not_dose_or_area():
    labels = np.zeros((3, 8, 8), dtype=int)
    labels[0, :6, :6] = 1
    labels[1, 1, 1] = 1
    labels[1, 5, 5] = 2
    assert choose_slice(labels) == 1


def test_metrics_identical_fields_agree_and_use_3d_targets():
    labels = np.zeros((9, 9, 9), dtype=int)
    labels[3:6, 3:6, 3:6] = 1
    dose = np.zeros(labels.shape)
    dose[labels == 1] = 20
    rows = metric_rows(dose, dose.copy(), labels, np.ones_like(labels, bool), 20, 1)
    assert rows[0]['reference_D98_Gy'] == 20
    assert rows[0]['reference_local_Paddick_CI'] == 1
    assert rows[0]['reference_local_GI'] == 1
    assert rows[0]['reference_local_V12_cc'] == 0
    assert rows[0]['volume_cc'] == .027
    assert rows[0]['predicted_D98_Gy'] == rows[0]['reference_D98_Gy']


def test_ct_background_is_fixed_synthetic_not_patient_input():
    a = synthetic_ct((-100, 100, -100, 100), (100, 100))
    assert a.shape == (100, 100)
    assert np.isfinite(a).all()
    assert a.min() < -500 and a.max() > 200
