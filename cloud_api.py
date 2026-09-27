"""Private account/archive routes and bounded, short-lived local downloads."""
import json
import hashlib
import os
import secrets
import threading
import time
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
import storage

router=APIRouter()
COOKIE='dose_atlas_session'
CACHE={}
CACHE_LOCK=threading.Lock()
CACHE_LIMIT=128*1024*1024
DOWNLOAD_TTL=600
CACHE_ENTRIES=32
LOGIN_ATTEMPTS={}


def same_origin(request):
    origin=request.headers.get('origin')
    expected=os.environ.get('PUBLIC_ORIGIN',str(request.base_url).rstrip('/'))
    if origin and origin.rstrip('/')!=expected.rstrip('/'):
        raise HTTPException(403,'Anfrage muss aus dieser Anwendung stammen.')


def require_account(request):
    token=request.cookies.get(COOKIE,'')
    if not storage.config_status()['enabled']:return None
    if not token:raise HTTPException(401,'Bitte zuerst anmelden.')
    storage.auth_get_user(token)
    return token


def cookie_options():
    return dict(httponly=True,secure=os.environ.get('PUBLIC_ORIGIN','').startswith('https://'),samesite='strict',path='/')


@router.get('/api/auth/session')
def session(request:Request):
    enabled=storage.config_status()['enabled']
    result=dict(enabled=enabled,authenticated=False,storage_mode='metadata_only',queue_enabled=True,is_admin=False)
    if enabled and request.cookies.get(COOKIE):
        try:
            user=storage.auth_get_user(request.cookies[COOKIE])
            from job_queue import is_admin
            result.update(authenticated=True,email=user.get('email',''),is_admin=is_admin(user))
        except storage.StorageError:pass
    return result


@router.post('/api/auth/login')
async def login(request:Request):
    same_origin(request)
    address=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with CACHE_LOCK:
        for ip in list(LOGIN_ATTEMPTS):
            LOGIN_ATTEMPTS[ip]=[t for t in LOGIN_ATTEMPTS[ip] if t>now-60]
            if not LOGIN_ATTEMPTS[ip]:LOGIN_ATTEMPTS.pop(ip)
        if len(LOGIN_ATTEMPTS.get(address,[]))>=5 or len(LOGIN_ATTEMPTS)>1000:
            raise HTTPException(429,'Zu viele Anmeldeversuche. Eine Minute warten.')
        LOGIN_ATTEMPTS.setdefault(address,[]).append(now)
    body=await request.json()
    email=body.get('email','');password=body.get('password','')
    if not isinstance(email,str) or not isinstance(password,str) or len(email)>254 or len(password)>1024:
        raise HTTPException(422,'Ungültige Zugangsdaten.')
    data=await run_in_threadpool(storage.auth_login,email,password)
    from job_queue import is_admin
    response=JSONResponse(dict(enabled=True,authenticated=True,email=data.get('user',{}).get('email',''),storage_mode='metadata_only',queue_enabled=True,is_admin=is_admin(data.get('user',{}))))
    response.set_cookie(COOKIE,data['access_token'],max_age=min(int(data.get('expires_in',3600)),86400),**cookie_options())
    return response


@router.post('/api/auth/logout')
def logout(request:Request):
    same_origin(request)
    response=JSONResponse(dict(enabled=storage.config_status()['enabled'],authenticated=False,storage_mode='metadata_only',queue_enabled=True,is_admin=False))
    response.delete_cookie(COOKIE,**cookie_options())
    return response


def links(base):
    return [dict(label='Vorhersage als RTDOSE',url=base+'/prediction.dcm'),
            dict(label='Zugehöriges RTSTRUCT',url=base+'/structure.dcm')]


def _download_owner(request, local_owner):
    # Exact bearer session plus independent browser cookie: switching accounts or
    # reauthenticating never inherits the former session's binary downloads.
    token=request.cookies.get(COOKIE,'')
    return hashlib.sha256((local_owner+'\0'+token).encode()).hexdigest()


def _remove_download(key):
    item=CACHE.pop(key,None)
    if item and item.get('timer'):item['timer'].cancel()


def _expire_download(key):
    with CACHE_LOCK:
        item=CACHE.get(key)
        if not item:return
        remaining=item['expires']-time.monotonic()
        if remaining<=0:
            _remove_download(key)
            return
        # Windows timers can wake fractionally early; retain the true deadline.
        timer=threading.Timer(max(.02,remaining),_expire_download,args=(key,))
        timer.daemon=True
        item['timer']=timer
        timer.start()


def finish_result(result,bundle,request):
    """Persist only compact metrics; paired DICOM remains in bounded RAM for 10 min."""
    files={name:bundle[name] for name in ('prediction.dcm','structure.dcm')}
    total=sum(map(len,files.values()))
    if total>CACHE_LIMIT:raise HTTPException(413,'Dosisexport überschreitet den temporären Downloadspeicher.')
    if storage.config_status()['enabled']:
        token=require_account(request)
        compact=storage.compact_result(result)
        persistent={'result.json':storage._json_bytes(compact)}
        if 'model_card.json' in bundle:
            persistent['model_card.json']=storage._json_bytes(storage.compact_model_card(json.loads(bundle['model_card.json'])))
        run=storage.store_analysis(token,persistent,compact['summary'])
        result['saved_run_id']=run['id']
    owner=request.cookies.get('dose_atlas_local') or secrets.token_urlsafe(32)
    key=secrets.token_urlsafe(24);now=time.monotonic()
    with CACHE_LOCK:
        for old in list(CACHE):
            if CACHE[old]['expires']<=now:_remove_download(old)
        while CACHE and (len(CACHE)>=CACHE_ENTRIES or sum(v['bytes'] for v in CACHE.values())+total>CACHE_LIMIT):
            _remove_download(next(iter(CACHE)))
        timer=threading.Timer(DOWNLOAD_TTL,_expire_download,args=(key,))
        timer.daemon=True
        CACHE[key]=dict(owner=_download_owner(request,owner),expires=now+DOWNLOAD_TTL,files=files,bytes=total,timer=timer)
        # Actual deadline purge runs even when no further HTTP request arrives.
        timer.start()
    result['downloads']=links('/api/downloads/'+key)
    result['download_expires_in_seconds']=DOWNLOAD_TTL
    result['download_expires_at']=time.time()+DOWNLOAD_TTL
    result['storage_mode']='metadata_only'
    response=JSONResponse(result,headers={'Cache-Control':'no-store'})
    response.set_cookie('dose_atlas_local',owner,max_age=3600,**cookie_options())
    return response


@router.get('/api/downloads/{key}/{name}')
def temporary_download(key:str,name:str,request:Request):
    # Revoking authorization also stops still-live DICOM downloads.
    require_account(request)
    owner=request.cookies.get('dose_atlas_local','')
    with CACHE_LOCK:
        item=CACHE.get(key)
        if item and item['expires']<=time.monotonic():_remove_download(key);item=None
        if not owner or not item or not secrets.compare_digest(item['owner'],_download_owner(request,owner)) or name not in item['files']:
            raise HTTPException(404,'Download abgelaufen oder nicht verfügbar.')
        data=item['files'][name]
    return Response(data,media_type='application/dicom',headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'no-store'})


@router.get('/api/runs')
def runs(request:Request):
    token=require_account(request)
    if not token:raise HTTPException(404,'Archiv nicht eingerichtet.')
    return dict(runs=storage.list_runs(token))


@router.get('/api/runs/{run_id}')
def run(run_id:str,request:Request):
    token=require_account(request)
    if not token:raise HTTPException(404,'Archiv nicht eingerichtet.')
    data=storage.load_run(token,run_id)
    result=data['result'];result['saved_run_id']=run_id
    result['downloads']=[]
    result['metadata_only']=True
    return result


@router.get('/api/runs/{run_id}/files/{name}')
def stored_file(run_id:str,name:str,request:Request):
    token=require_account(request)
    if not token:raise HTTPException(404,'Archiv nicht eingerichtet.')
    data,mime=storage.download_file(token,run_id,name)
    return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="{name}"'})
