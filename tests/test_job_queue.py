import asyncio
import threading
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import app as api
import job_queue as jq
import storage
from test_api import structure_bytes


def test_fifo_owner_cancel_and_non_eviction():
    q=jq.AdmissionQueue();a=q.reserve('a','inspect');b=q.reserve('b','predict');c=q.reserve('c','inspect')
    assert a['state']=='ready' and b['position']==1 and c['position']==2
    with pytest.raises(HTTPException) as exc:q.claim(a['id'],'b','inspect')
    assert exc.value.status_code==404
    with pytest.raises(HTTPException):q.claim(a['id'],'a','predict')
    q.claim(a['id'],'a','inspect')
    with pytest.raises(HTTPException):q.cancel(a['id'],'a')
    assert q.clear()==2
    assert q.status(a['id'],'a')['state']=='running'
    q.finish(a['id']);assert q.status(a['id'],'a')['state']=='completed'


def test_expiry_poll_and_bounds():
    now=[0];q=jq.AdmissionQueue(limit=2,clock=lambda:now[0]);a=q.reserve('a','inspect');b=q.reserve('b','inspect')
    with pytest.raises(HTTPException):q.reserve('a','inspect')
    with pytest.raises(HTTPException):q.reserve('c','inspect')
    now[0]=46
    assert q.status(b['id'],'b')['state']=='ready'
    assert q.status(a['id'],'a')['state']=='expired'
    q.claim(b['id'],'b','inspect');c=q.reserve('c','inspect')
    now[0]=150;assert q.status(c['id'],'c')['state']=='waiting'
    now[0]=271;assert q.status(c['id'],'c')['state']=='expired'
    assert q.status(b['id'],'b')['state']=='running'
    q.finish(b['id'])
    for i in range(30):
        ticket=q.reserve(str(i),'inspect');q.cancel(ticket['id'],str(i))
    assert len(q.jobs)<=4


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':False})
    monkeypatch.setattr(jq,'QUEUE',jq.AdmissionQueue())
    with TestClient(api.app) as client:yield client


def test_local_ticket_success_error_release_and_isolation(local):
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    assert local.cookies.get(jq.LOCAL_COOKIE)
    assert local.get('/api/auth/session').json()['queue_enabled']
    with TestClient(api.app) as other:
        other.post('/api/queue',json={'operation':'inspect'})
        assert other.get('/api/queue/'+ticket['id']).status_code==404
        assert other.delete('/api/queue/'+ticket['id']).status_code==404
    data,_=structure_bytes()
    r=local.post('/api/inspect',headers={'X-Dose-Job':ticket['id']},files={'structure':('synthetic.dcm',data)})
    assert r.status_code==200,r.text
    assert local.get('/api/queue/'+ticket['id']).json()['state']=='completed'
    assert not api.COMPUTE_LOCK.locked()


def test_validation_error_releases_next(local):
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    r=local.post('/api/inspect',headers={'X-Dose-Job':ticket['id']},content=b'bad')
    assert r.status_code==422
    assert local.get('/api/queue/'+ticket['id']).json()['state']=='completed'
    assert not api.COMPUTE_LOCK.locked()
    assert local.post('/api/queue',json={'operation':'predict'}).json()['state']=='ready'


def test_small_control_bounds_csrf_and_metadata(local):
    assert local.post('/api/queue',content=b'x'*4097).status_code==413
    assert local.post('/api/queue',json={'operation':'inspect','patient':'no'}).status_code==422
    assert local.post('/api/queue',json={'operation':'inspect'},headers={'Origin':'https://evil.invalid'}).status_code==403
    local.post('/api/queue',json={'operation':'inspect'})
    assert local.post('/api/queue/clear').status_code==403


def test_hosted_auth_admin_and_csrf(local,monkeypatch):
    monkeypatch.setattr(jq,'ADMIN_EMAIL','admin@example.invalid')
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    users={'user':{'email':'other@example.org','email_confirmed_at':'date'},'unverified':{'email':jq.ADMIN_EMAIL},
           'admin':{'email':jq.ADMIN_EMAIL,'email_confirmed_at':'date'}}
    def auth(token):
        if token not in users:raise storage.StorageError('Nicht angemeldet.',401)
        return users[token]
    monkeypatch.setattr(storage,'auth_get_user',auth)
    assert local.post('/api/queue',json={'operation':'inspect'}).status_code==401
    assert local.get('/api/queue').status_code==401
    local.cookies.set('dose_atlas_session','user')
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    assert local.post('/api/inspect',content=b'bad').status_code==428
    assert local.post('/api/queue/clear').status_code==403
    local.cookies.set('dose_atlas_session','unverified')
    assert local.post('/api/queue/clear').status_code==403
    local.cookies.set('dose_atlas_session','admin')
    assert local.get('/api/auth/session').json()['is_admin']
    assert local.get('/api/queue').json()['is_admin']
    assert local.get('/api/queue/'+ticket['id']).status_code==404
    assert local.post('/api/queue/clear',headers={'Origin':'https://evil.invalid'}).status_code==403
    assert local.post('/api/queue/clear').json()=={'cleared':1}


def test_reject_before_read_and_disconnect_release(local):
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    owner_cookie=local.cookies.get(jq.LOCAL_COOKIE)
    async def run():
        messages=[]
        async def underlying(scope,receive,send):raise AssertionError('must not dispatch')
        async def unread():raise AssertionError('rejected body was read')
        async def send(message):messages.append(message)
        middleware=api.SizeLimit(underlying)
        scope={'type':'http','method':'POST','path':'/api/inspect','headers':[(b'x-dose-job',b'not-a-ticket'),(b'cookie',f'{jq.LOCAL_COOKIE}={owner_cookie}'.encode())],'scheme':'http','server':('testserver',80),'query_string':b''}
        await middleware(scope,unread,send)
        assert messages[0]['status']==404
        assert not api.COMPUTE_LOCK.locked()
        scope['headers'][0]=(b'x-dose-job',ticket['id'].encode())
        async def disconnect():return {'type':'http.disconnect'}
        await middleware(scope,disconnect,send)
        assert not api.COMPUTE_LOCK.locked()
    asyncio.run(run())
    assert local.get('/api/queue/'+ticket['id']).json()['state']=='completed'


def test_controls_available_during_active_upload(local):
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    owner_cookie=local.cookies.get(jq.LOCAL_COOKIE)
    async def run():
        entered=asyncio.Event();release=asyncio.Event();messages=[]
        async def underlying(scope,receive,send):
            assert scope['path']=='/api/queue'
            await send({'type':'http.response.start','status':200,'headers':[]})
        middleware=api.SizeLimit(underlying)
        scope={'type':'http','method':'POST','path':'/api/inspect','headers':[(b'x-dose-job',ticket['id'].encode()),(b'cookie',f'{jq.LOCAL_COOKIE}={owner_cookie}'.encode())],'scheme':'http','server':('testserver',80),'query_string':b''}
        async def slow():entered.set();await release.wait();return {'type':'http.disconnect'}
        async def send(message):messages.append(message)
        pending=asyncio.create_task(middleware(scope,slow,send));await entered.wait()
        async def empty():return {'type':'http.request','body':b''}
        await middleware(dict(scope,path='/api/queue'),empty,send)
        assert messages[0]['status']==200
        assert jq.QUEUE.counts()['active']==1
        release.set();await pending
    asyncio.run(run())

def test_fifo_completion_and_atomic_claim():
    from concurrent.futures import ThreadPoolExecutor
    q=jq.AdmissionQueue();a=q.reserve('a','inspect');b=q.reserve('b','predict');c=q.reserve('c','inspect')
    def claim():
        try:q.claim(a['id'],'a','inspect');return True
        except HTTPException:return False
    with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(lambda _:claim(),range(8)))==1
    q.finish(a['id'])
    assert q.status(b['id'],'b')['state']=='ready'
    assert q.status(c['id'],'c')['position']==1
    q.cancel(b['id'],'b')
    assert q.status(c['id'],'c')['state']=='ready'


def test_compute_failure_release_and_demo_priority(local,monkeypatch):
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    assert local.get('/api/demo').status_code==429
    def fail(*args):raise RuntimeError('sensitive synthetic filename')
    monkeypatch.setattr(api,'inspect_structure',fail)
    data,_=structure_bytes()
    response=local.post('/api/inspect',headers={'X-Dose-Job':ticket['id']},files={'structure':('synthetic.dcm',data)})
    assert response.status_code==422 and 'sensitive' not in response.text
    assert not api.COMPUTE_LOCK.locked()
    assert local.get('/api/queue/'+ticket['id']).json()['state']=='completed'

def test_stalled_upload_timeout_releases_admission(local,monkeypatch):
    monkeypatch.setattr(api,'UPLOAD_IDLE_TIMEOUT',.01)
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    owner_cookie=local.cookies.get(jq.LOCAL_COOKIE)
    next_ticket=jq.QUEUE.reserve('next-owner','inspect')
    async def run():
        messages=[];cancelled=[]
        async def underlying(scope,receive,send):raise AssertionError('timed out upload must not dispatch')
        async def stalled():
            try:await asyncio.Event().wait()
            finally:cancelled.append(True)
        async def send(message):messages.append(message)
        middleware=api.SizeLimit(underlying)
        scope={'type':'http','method':'POST','path':'/api/inspect','headers':[(b'x-dose-job',ticket['id'].encode()),(b'cookie',f'{jq.LOCAL_COOKIE}={owner_cookie}'.encode())],'scheme':'http','server':('testserver',80),'query_string':b''}
        await middleware(scope,stalled,send)
        assert messages[0]['status']==408
        assert cancelled and not middleware.upload_lock.locked() and not api.COMPUTE_LOCK.locked()
    asyncio.run(run())
    assert local.get('/api/queue/'+ticket['id']).json()['state']=='completed'
    assert jq.QUEUE.status(next_ticket['id'],'next-owner')['state']=='ready'


def test_revoked_auth_rejected_before_upload_body(local,monkeypatch):
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':True})
    monkeypatch.setattr(storage,'auth_get_user',lambda token:{'id':'test'})
    local.cookies.set('dose_atlas_session','test-token')
    ticket=local.post('/api/queue',json={'operation':'inspect'}).json()
    def revoked(token):raise storage.StorageError('Sitzung abgelaufen.',401)
    monkeypatch.setattr(storage,'auth_get_user',revoked)
    async def run():
        messages=[]
        async def underlying(scope,receive,send):raise AssertionError('revoked authentication must not dispatch')
        async def unread():raise AssertionError('revoked body was read')
        async def send(message):messages.append(message)
        scope={'type':'http','method':'POST','path':'/api/inspect','headers':[(b'x-dose-job',ticket['id'].encode()),(b'cookie',b'dose_atlas_session=test-token')],'scheme':'http','server':('testserver',80),'query_string':b''}
        await api.SizeLimit(underlying)(scope,unread,send)
        assert messages[0]['status']==401
        assert not api.COMPUTE_LOCK.locked()
    asyncio.run(run())

def test_existing_demo_keeps_new_ticket_waiting_until_release():
    q=jq.AdmissionQueue();compute_lock=threading.Lock()
    assert q.acquire_unqueued(compute_lock)
    assert q.counts()=={'waiting':0,'active':1}
    ticket=q.reserve('queued-owner','inspect')
    assert ticket['state']=='waiting' and ticket['position']==1 and ticket['active']==1
    with pytest.raises(HTTPException):q.claim(ticket['id'],'queued-owner','inspect')
    assert not q.acquire_unqueued(compute_lock)
    q.release_unqueued(compute_lock)
    assert not compute_lock.locked()
    assert q.status(ticket['id'],'queued-owner')['state']=='ready'
    assert q.counts()=={'waiting':0,'active':1}
    assert not q.acquire_unqueued(compute_lock)
