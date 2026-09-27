from io import BytesIO
import numpy as np
import pydicom
from fastapi.testclient import TestClient
from app import app
import cloud_api
import storage
from test_api import structure_bytes

def test_local_download_roundtrip_is_session_bound(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL',raising=False)
    # Exercise the export/session contract with a synthetic fitted estimator,
    # without depending on privately distributed clinical model weights.
    import app as api
    from sklearn.dummy import DummyRegressor
    estimator=DummyRegressor(strategy='constant',constant=1.2).fit(np.zeros((2,8)),[1.2,1.2])
    monkeypatch.setattr(api,'load_model',lambda:estimator)
    data,_=structure_bytes()
    with TestClient(app) as owner,TestClient(app) as stranger:
        response=owner.post('/api/predict',files={'structure':('synthetic.dcm',data)},
            data={'targets':'[1]','prescription':'20','anonymize':'true'})
        assert response.status_code==200,response.text[:100]
        result=response.json();links=result['downloads']
        dose=owner.get(links[0]['url']);struct=owner.get(links[1]['url'])
        assert dose.status_code==struct.status_code==200
        rd=pydicom.dcmread(BytesIO(dose.content));rs=pydicom.dcmread(BytesIO(struct.content))
        assert rd.StudyInstanceUID==rs.StudyInstanceUID
        assert rd.FrameOfReferenceUID==rs.StructureSetROISequence[0].ReferencedFrameOfReferenceUID
        assert rd.pixel_array.shape[0]>2 and np.max(rd.pixel_array)*rd.DoseGridScaling>20
        assert stranger.get(links[0]['url']).status_code==404

def test_cloud_requires_account_and_anonymization(monkeypatch):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    monkeypatch.setattr(storage,'auth_get_user',lambda token:{'id':'demo'})
    data,_=structure_bytes()
    with TestClient(app) as client:
        assert client.post('/api/inspect',files={'structure':('s.dcm',data)}).status_code==401
        client.cookies.set(cloud_api.COOKIE,'authenticated-test-token')
        ticket=client.post('/api/queue',json={'operation':'inspect'}).json()['id']
        assert client.post('/api/inspect',files={'structure':('s.dcm',data)},data={'anonymize':'false'},headers={'X-Dose-Job':ticket}).status_code==422

def test_foreign_origin_cannot_login_or_submit(monkeypatch):
    monkeypatch.setenv('PUBLIC_ORIGIN','https://dose-atlas.kiragroh.cloud')
    with TestClient(app) as client:
        response=client.post('/api/auth/login',json={'email':'x','password':'y'},headers={'Origin':'https://elsewhere.invalid'})
        assert response.status_code==403

def test_cloud_persists_metadata_only_and_download_token_isolation(monkeypatch):
    from starlette.requests import Request
    import json
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    monkeypatch.setattr(storage,'auth_get_user',lambda token:{'id':'demo'})
    saved=[]
    monkeypatch.setattr(storage,'store_analysis',lambda token,bundle,summary: saved.append(bundle) or {'id':'run'})
    request=Request({'type':'http','headers':[(b'cookie',b'dose_atlas_session=A; dose_atlas_local=browser') ]})
    response=cloud_api.finish_result({'summary':{},'slices':{'predicted':[[[9]]]}},
        {'prediction.dcm':b'dose','structure.dcm':b'struct'},request)
    result=json.loads(response.body)
    assert list(saved[0])==['result.json'] and b'slices' not in saved[0]['result.json']
    assert result['download_expires_in_seconds']==600
    with TestClient(app) as client:
        client.cookies.set('dose_atlas_local','browser')
        client.cookies.set(cloud_api.COOKIE,'A')
        assert client.get(result['downloads'][0]['url']).status_code==200
        client.cookies.set(cloud_api.COOKIE,'B')
        assert client.get(result['downloads'][0]['url']).status_code==404

def test_download_expires_without_any_request_and_cache_is_bounded(monkeypatch):
    import json,time
    from starlette.requests import Request
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':False})
    monkeypatch.setattr(cloud_api,'DOWNLOAD_TTL',.05)
    monkeypatch.setattr(cloud_api,'CACHE_LIMIT',12)
    with cloud_api.CACHE_LOCK:cloud_api.CACHE.clear()
    request=Request({'type':'http','headers':[]})
    bundle={'prediction.dcm':b'1234','structure.dcm':b'5678'}
    cloud_api.finish_result({'summary':{}},bundle,request)
    result=json.loads(cloud_api.finish_result({'summary':{}},bundle,request).body)
    assert len(cloud_api.CACHE)==1 and sum(x['bytes'] for x in cloud_api.CACHE.values())<=12
    deadline=time.monotonic()+1
    while cloud_api.CACHE and time.monotonic()<deadline:time.sleep(.01)
    assert not cloud_api.CACHE
    with TestClient(app) as client:assert client.get(result['downloads'][0]['url']).status_code==404


def test_revoked_account_cannot_login_or_download(monkeypatch):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    def denied(*args):raise storage.StorageError('Zugang nicht freigeschaltet.',403)
    monkeypatch.setattr(storage,'auth_login',denied)
    monkeypatch.setattr(storage,'auth_get_user',denied)
    cloud_api.LOGIN_ATTEMPTS.clear()
    with TestClient(app) as client:
        result=client.post('/api/auth/login',json={'email':'user@example.invalid','password':'long-password'})
        assert result.status_code==403 and 'set-cookie' not in result.headers
        client.cookies.set(cloud_api.COOKIE,'formerly-allowed-token')
        assert client.get('/api/downloads/key/prediction.dcm').status_code==403
