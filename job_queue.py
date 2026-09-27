"""Bounded admission tickets only: no upload bytes, filenames or patient metadata."""
import hashlib
import secrets
import threading
import time
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
import cloud_api
import storage

ADMIN_EMAIL=os.environ.get('DOSE_ATLAS_ADMIN_EMAIL','').strip()
LOCAL_COOKIE='dose_atlas_queue_owner'
TERMINAL={'completed','cancelled','expired'}


def is_admin(user):
    return bool(ADMIN_EMAIL and user and user.get('email')==ADMIN_EMAIL and user.get('email_confirmed_at'))


class AdmissionQueue:
    def __init__(self,limit=64,clock=time.monotonic,waiting_ttl=120,ready_ttl=45):
        self.limit=limit;self.clock=clock;self.waiting_ttl=waiting_ttl;self.ready_ttl=ready_ttl
        self.lock=threading.Lock();self.jobs={};self.external_active=False

    def _refresh(self):
        now=self.clock()
        for key,job in list(self.jobs.items()):
            if job['state'] in TERMINAL:
                if job['expires']<=now:del self.jobs[key]
            elif job['state']!='running' and job['expires']<=now:
                job.update(state='expired',expires=now+120)
        if not self.external_active and not any(j['state'] in ('ready','running') for j in self.jobs.values()):
            for job in self.jobs.values():
                if job['state']=='waiting':
                    job.update(state='ready',expires=now+self.ready_ttl);break

    def _counts(self):
        return dict(waiting=sum(j['state']=='waiting' for j in self.jobs.values()),
                    active=int(self.external_active)+sum(j['state'] in ('ready','running') for j in self.jobs.values()))

    def _owned(self,key,owner):
        job=self.jobs.get(key)
        if not job or not secrets.compare_digest(job['owner'],owner):
            raise HTTPException(404,'Auftrag nicht verfügbar.')
        return job

    def _view(self,key):
        job=self.jobs[key];position=0
        if job['state']=='waiting':
            for k,j in self.jobs.items():
                if j['state']=='waiting':position+=1
                if k==key:break
        return dict(id=key,state=job['state'],position=position,**self._counts())

    def reserve(self,owner,operation):
        if operation not in ('inspect','predict'):raise HTTPException(422,'Ungültiger Auftrag.')
        with self.lock:
            self._refresh()
            if any(j['owner']==owner and j['state'] not in TERMINAL for j in self.jobs.values()):
                raise HTTPException(409,'Für diese Sitzung besteht bereits ein Auftrag.')
            if sum(j['state'] not in TERMINAL for j in self.jobs.values())>=self.limit:
                raise HTTPException(429,'Warteschlange voll. Bitte später erneut versuchen.')
            # Bound retained terminal records as well as live reservations.
            while len(self.jobs)>=self.limit*2:
                old=next((k for k,j in self.jobs.items() if j['state'] in TERMINAL),None)
                if old is None:break
                del self.jobs[old]
            key=secrets.token_urlsafe(32)
            self.jobs[key]=dict(owner=owner,operation=operation,state='waiting',expires=self.clock()+self.waiting_ttl)
            self._refresh();return self._view(key)

    def status(self,key,owner):
        with self.lock:
            self._refresh();job=self._owned(key,owner)
            if job['state']=='waiting':job['expires']=self.clock()+self.waiting_ttl
            return self._view(key)

    def counts(self):
        with self.lock:self._refresh();return self._counts()

    def acquire_unqueued(self,compute_lock):
        # Atomic with reserve(): a demo cannot take a slot already promised to
        # a ready ticket. Nonblocking acquire avoids lock-order deadlocks.
        with self.lock:
            self._refresh()
            if self._counts()['active'] or not compute_lock.acquire(blocking=False):return False
            self.external_active=True
            return True

    def release_unqueued(self,compute_lock):
        with self.lock:
            compute_lock.release()
            self.external_active=False
            self._refresh()

    def claim(self,key,owner,operation):
        with self.lock:
            self._refresh();job=self._owned(key,owner)
            if job['operation']!=operation or job['state']!='ready':raise HTTPException(409,'Auftrag ist nicht zur Verarbeitung bereit.')
            job['state']='running'

    def finish(self,key):
        with self.lock:
            job=self.jobs.get(key)
            if job and job['state']=='running':job.update(state='completed',expires=self.clock()+120)
            self._refresh()

    def cancel(self,key,owner):
        with self.lock:
            self._refresh();job=self._owned(key,owner)
            if job['state']=='running':raise HTTPException(409,'Laufende Verarbeitung kann nicht entfernt werden.')
            if job['state'] not in TERMINAL:job.update(state='cancelled',expires=self.clock()+120)
            self._refresh();return self._view(key)

    def clear(self):
        with self.lock:
            self._refresh();count=0
            for job in self.jobs.values():
                if job['state'] in ('waiting','ready'):
                    job.update(state='cancelled',expires=self.clock()+120);count+=1
            return count


QUEUE=AdmissionQueue()
router=APIRouter()


def identity(request,create=False):
    if storage.config_status()['enabled']:
        token=request.cookies.get(cloud_api.COOKIE,'')
        if not token:raise HTTPException(401,'Bitte zuerst anmelden.')
        user=storage.auth_get_user(token)
        return hashlib.sha256(token.encode()).hexdigest(),is_admin(user),None
    owner=request.cookies.get(LOCAL_COOKIE,'')
    if not owner:
        if not create:raise HTTPException(401,'Keine Warteschlangen-Sitzung vorhanden.')
        owner=secrets.token_urlsafe(32)
        return hashlib.sha256(owner.encode()).hexdigest(),False,owner
    return hashlib.sha256(owner.encode()).hexdigest(),False,None


@router.post('/api/queue')
async def reserve(request:Request):
    cloud_api.same_origin(request)
    owner,admin,cookie=await run_in_threadpool(identity,request,True)
    try:body=await request.json()
    except ValueError:raise HTTPException(422,'Ungültiger Auftrag.')
    if not isinstance(body,dict) or set(body)!={'operation'}:raise HTTPException(422,'Nur Auftragstyp erlaubt.')
    result=QUEUE.reserve(owner,body['operation'])
    response=JSONResponse(result)
    if cookie:response.set_cookie(LOCAL_COOKIE,cookie,max_age=86400,**cloud_api.cookie_options())
    return response


@router.get('/api/queue')
def overview(request:Request):
    owner,admin,cookie=identity(request,True)
    return dict(**QUEUE.counts(),is_admin=admin)


@router.post('/api/queue/clear')
def clear(request:Request):
    cloud_api.same_origin(request)
    owner,admin,cookie=identity(request)
    if not admin:raise HTTPException(403,'Nur für die verifizierte Administration.')
    return dict(cleared=QUEUE.clear())


@router.get('/api/queue/{key}')
def status(key:str,request:Request):
    owner,admin,cookie=identity(request)
    return QUEUE.status(key,owner)


@router.delete('/api/queue/{key}')
def cancel(key:str,request:Request):
    cloud_api.same_origin(request)
    owner,admin,cookie=identity(request)
    return QUEUE.cancel(key,owner)
