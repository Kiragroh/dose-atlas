from fastapi.testclient import TestClient
from app import app
import auth_activation
import cloud_api
import storage
import pytest

@pytest.fixture(autouse=True)
def allowlisted(monkeypatch):
    monkeypatch.setattr(storage, "auth_get_user", lambda token: {"id":"test-user", "email":"synthetic@example.invalid"})


def test_invitation_verifies_before_password_change(monkeypatch):
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return b'{"access_token":"verified-token","user":{"email":"synthetic@example.invalid"}}'
    monkeypatch.setattr(storage, '_request', request)
    result = auth_activation.redeem('a'*64, 'invite')
    auth_activation.update_password(result['access_token'], 'synthetic-password')
    assert result['access_token'] == 'verified-token'
    assert calls[0][1] == '/auth/v1/verify'
    assert calls[1][0:2] == ('PUT', '/auth/v1/user')
    assert calls[1][2]['token'] == 'verified-token'


def test_activation_validates_before_consuming_link(monkeypatch):
    monkeypatch.setattr(storage, 'config_status', lambda: {'enabled': True})
    monkeypatch.setattr(auth_activation, 'redeem', lambda *a: (_ for _ in ()).throw(AssertionError('consumed')))
    cloud_api.LOGIN_ATTEMPTS.clear()
    with TestClient(app) as client:
        assert client.post('/api/auth/activate', json={'token_hash':'a'*64,'type':'invite','password':'short'}).status_code == 422
        assert client.post('/api/auth/activate', json={'token_hash':'a'*64,'type':'signup','password':'long-password'}).status_code == 422
        assert client.post('/api/auth/activate', json=[]).status_code == 422


def test_activation_secure_cookie_no_token_in_response(monkeypatch):
    monkeypatch.setenv('PUBLIC_ORIGIN', 'https://dose-atlas.kiragroh.cloud')
    monkeypatch.setattr(storage, 'config_status', lambda: {'enabled': True})
    monkeypatch.setattr(auth_activation, 'redeem', lambda *a: {'access_token':'private-token','user':{'email':'synthetic@example.invalid'}})
    cloud_api.LOGIN_ATTEMPTS.clear()
    monkeypatch.setattr(auth_activation, 'update_password', lambda *a: {'email':'synthetic@example.invalid'})
    with TestClient(app) as client:
        result = client.post('/api/auth/activate', json={'token_hash':'a'*64,'type':'invite','password':'long-password'})
        assert result.status_code == 200 and result.json()['authenticated']
        assert 'private-token' not in result.text
        assert all(flag in result.headers['set-cookie'] for flag in ['HttpOnly','Secure','SameSite=strict'])
        assert client.post('/api/auth/activate', json={}, headers={'Origin':'https://elsewhere.invalid'}).status_code == 403


def test_password_failure_retry_reuses_verified_session_and_clears_cookies(monkeypatch):
    calls=[]
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    monkeypatch.setenv('PUBLIC_ORIGIN','http://testserver')
    cloud_api.LOGIN_ATTEMPTS.clear()
    def request(method,path,**kwargs):
        calls.append((method,path,kwargs))
        if path=='/auth/v1/verify':return b'{"access_token":"retry-secret"}'
        if len(calls)==2:raise storage.StorageError('upstream rejected',400)
        return b'{"email":"test@example.invalid"}'
    monkeypatch.setattr(storage,'_request',request)
    body={'token_hash':'a'*64,'type':'invite','password':'long-password'}
    with TestClient(app) as client:
        failed=client.post('/api/auth/activate',json=body)
        assert failed.status_code==400 and 'retry-secret' not in failed.text
        assert cloud_api.COOKIE not in client.cookies
        assert 'HttpOnly' in failed.headers['set-cookie']
        assert client.cookies.get(auth_activation.ACTIVATION_COOKIE)=='retry-secret'
        result=client.post('/api/auth/activate',json=body)
        assert result.status_code==200
        assert client.cookies.get(cloud_api.COOKIE)=='retry-secret'
        assert auth_activation.ACTIVATION_COOKIE not in client.cookies
        assert auth_activation.BINDING_COOKIE not in client.cookies
    assert [path for _,path,_ in calls].count('/auth/v1/verify')==1
    assert calls[-1][2]['token']=='retry-secret'


def test_retry_binding_rejects_another_link_tamper_and_expiry(monkeypatch):
    import time
    from starlette.requests import Request
    token='token';token_hash='a'*64;expires=int(time.time())+600
    signature=auth_activation._binding(token,token_hash,'invite',expires)
    def request(binding,token_value=token):
        cookie=f'{auth_activation.ACTIVATION_COOKIE}={token_value}; {auth_activation.BINDING_COOKIE}={binding}'
        return Request({'type':'http','headers':[(b'cookie',cookie.encode())]})
    req=request(str(expires)+'.'+signature)
    assert auth_activation._retry_token(req,token_hash,'invite')==token
    assert auth_activation._retry_token(req,'b'*64,'invite') is None
    assert auth_activation._retry_token(req,token_hash,'recovery') is None
    assert auth_activation._retry_token(request(str(expires)+'.'+signature,'other'),token_hash,'invite') is None
    monkeypatch.setattr(auth_activation.time,'time',lambda:expires+1)
    assert auth_activation._retry_token(req,token_hash,'invite') is None


@pytest.mark.parametrize('email',[None,'other@example.invalid'])
def test_not_allowed_or_email_mismatch_never_changes_password_or_sets_cookie(monkeypatch,email):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    monkeypatch.setattr(auth_activation,'redeem',lambda *a:{'access_token':'verified'})
    if email is None:
        def denied(token):raise storage.StorageError('Zugang nicht freigeschaltet.',403)
        monkeypatch.setattr(storage,'auth_get_user',denied)
    monkeypatch.setattr(auth_activation,'update_password',lambda *a: (_ for _ in ()).throw(AssertionError('password mutation')))
    cloud_api.LOGIN_ATTEMPTS.clear()
    body={'token_hash':'a'*64,'type':'invite','password':'long-password'}
    if email is not None:body['email']=email
    with TestClient(app) as client:
        response=client.post('/api/auth/activate',json=body)
        assert response.status_code==403 and response.json()['detail']=='Zugang nicht freigeschaltet.'
        assert cloud_api.COOKIE not in client.cookies
        if email is None: assert 'set-cookie' not in response.headers
        else: assert client.cookies.get(auth_activation.ACTIVATION_COOKIE)=='verified'


def test_registration_email_normalized_and_retry_rechecks_access(monkeypatch):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    monkeypatch.setattr(auth_activation,'redeem',lambda *a:{'access_token':'verified'})
    def fail(*a):raise storage.StorageError('Temporary failure',503)
    monkeypatch.setattr(auth_activation,'update_password',fail)
    cloud_api.LOGIN_ATTEMPTS.clear()
    body={'token_hash':'a'*64,'type':'invite','password':'long-password','email':' SYNTHETIC@EXAMPLE.INVALID '}
    with TestClient(app) as client:
        response=client.post('/api/auth/activate',json=body)
        assert response.status_code==503
        def denied(token):raise storage.StorageError('Zugang nicht freigeschaltet.',403)
        monkeypatch.setattr(storage,'auth_get_user',denied)
        monkeypatch.setattr(auth_activation,'redeem',lambda *a: (_ for _ in ()).throw(AssertionError('OTP consumed twice')))
        assert client.post('/api/auth/activate',json=body).status_code==403


def test_email_typo_can_be_corrected_without_consuming_invitation_again(monkeypatch):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    calls=[]
    def redeem(*args):
        calls.append('verify')
        return {'access_token':'verified'}
    monkeypatch.setattr(auth_activation,'redeem',redeem)
    def update(*args):
        calls.append('password')
        return {'email':'synthetic@example.invalid'}
    monkeypatch.setattr(auth_activation,'update_password',update)
    cloud_api.LOGIN_ATTEMPTS.clear()
    body={'token_hash':'a'*64,'type':'invite','password':'long-password','email':'typo@example.invalid'}
    with TestClient(app) as client:
        response=client.post('/api/auth/activate',json=body)
        assert response.status_code==403 and calls==['verify']
        assert cloud_api.COOKIE not in client.cookies
        assert client.cookies.get(auth_activation.ACTIVATION_COOKIE)=='verified'
        body['email']='synthetic@example.invalid'
        response=client.post('/api/auth/activate',json=body)
        assert response.status_code==200 and calls==['verify','password']
        assert client.cookies.get(cloud_api.COOKIE)=='verified'
        assert auth_activation.ACTIVATION_COOKIE not in client.cookies
