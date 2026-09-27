"""Clean source checkout: no private weights, data or default administrator."""
import io
import zipfile
import pytest
from fastapi.testclient import TestClient
import app as api
import job_queue
from geometry import InputError


def test_demo_without_model_is_explicit_analytic_demo(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'ROOT', tmp_path)
    api.load_model.cache_clear()
    with TestClient(api.app) as client:
        response = client.get('/api/demo')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['summary']['synthetic_demo'] is True
    assert result['summary']['prediction_source'] == 'analytic_demo_not_trained'
    assert result['summary']['target_count'] == 6
    assert len(result['dvhs']) == 6
    assert any('NOT a trained prediction' in message for message in result['warnings'])


def test_real_inference_never_falls_back_to_analytic_demo(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'ROOT', tmp_path)
    api.load_model.cache_clear()
    with pytest.raises(InputError):
        api.load_model()
    assert api.model_card()['status'] == 'Model weights not installed'


def test_public_source_contains_runtime_dependencies_not_weights():
    with TestClient(api.app) as client:
        response = client.get('/api/source')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    for name in ('dose_calibration.py', 'overview_model.py', 'CITATION.cff'):
        assert 'dose-atlas/' + name in names
    assert 'dose-atlas/docs/assets/dose-atlas-banner.png' in names
    assert not any('/private/' in name or name.endswith(('.joblib', '.npz', '.dcm')) for name in names)


def test_no_unconfigured_admin_grant(monkeypatch):
    monkeypatch.setattr(job_queue, 'ADMIN_EMAIL', '')
    assert not job_queue.is_admin({'email': '', 'email_confirmed_at': 'date'})
