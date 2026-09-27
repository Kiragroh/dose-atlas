import unittest
import numpy as np
from sklearn.dummy import DummyRegressor


class OverviewModelTests(unittest.TestCase):
    def test_blend_retains_near_model_and_uses_distal_model(self):
        import overview_model as om
        X = np.zeros((5, 8), np.float32)
        X[:, 0] = [-2, 20, 30, 37.5, 45]
        near = DummyRegressor(strategy='constant', constant=.8).fit(X, X[:, 0])
        far = DummyRegressor(strategy='constant', constant=.2).fit(X, X[:, 0])
        original = X.copy()
        np.testing.assert_allclose(om.predict_overview(near, far, X), [.8, .8, .8, .5, .2])
        np.testing.assert_array_equal(X, original)

    def test_training_samples_never_use_unknown_dose(self):
        import overview_model as om
        X = np.zeros((600, 8), np.float32)
        X[:, 0] = np.linspace(20, 150, 600)
        y = np.ones(600, np.float32); y[::2] = np.nan
        a, b = om.distal_samples(X, y, 1, per_band=20)
        self.assertTrue(np.isfinite(b).all())
        self.assertGreater(a[:, 0].max(), 100)
        self.assertGreaterEqual(a[:, 0].min(), 25)

