"""Local stateless research API; user DICOM objects are not persisted."""
import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
import json
import asyncio
import hashlib
import threading
import time
import zipfile
from io import BytesIO
from functools import lru_cache
from pathlib import Path
import joblib
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser
from starlette.concurrency import run_in_threadpool
from geometry import InputError, read_dicom, structure_rois, structure_frame, make_grid, rasterize, sample_dose, roi_bounds,Grid,display_polyline,display_target_contours,display_isodoses
from dose_calibration import calibration_card, calibration_factor
from model import regularize_prediction, prescription_values, scale_prescriptions, geometry_features, predict, baseline
from metrics import dose_metrics, dvh, global_metrics, ring_benchmark, local_target_metrics
from upload import inspect_upload,prepare_upload
import local_cases
import storage
import cloud_api
import job_queue
import auth_activation
from dose_export import export_prediction,export_structure

ROOT=Path(__file__).resolve().parent
if os.environ.get('DOSE_ATLAS_HOSTED')=='1' and not storage.config_status()['enabled']:
    raise RuntimeError('Hosted mode requires configured private storage and authentication.')
MAX_BYTES=250*1024*1024
UPLOAD_IDLE_TIMEOUT=60
# A bounded request is parsed into memory; do not spool patient files to disk.
MultiPartParser.spool_max_size=MAX_BYTES+1
COMPUTE_LOCK=threading.Lock()


class SizeLimit:
    def __init__(self,app):
        self.app=app
        self.upload_lock=threading.Lock()
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]));path=scope.get('path','')
        control=path=='/api/queue' or path.startswith('/api/queue/')
        limit=4096 if control else MAX_BYTES
        try: length=int(headers.get(b'content-length',b'0'))
        except ValueError: length=limit+1
        if length<0 or length>limit:
            return await JSONResponse({'detail':'Anfrage zu groß.'},status_code=413)(scope,receive,send)
        if scope['method'] not in ('POST','DELETE'):return await self.app(scope,receive,send)
        claimed=None;compute_reserved=False;upload_reserved=False
        try:
            if scope['method']=='POST' and path in ('/api/inspect','/api/predict'):
                request=Request(scope)
                cloud_api.same_origin(request)
                ticket=request.headers.get('x-dose-job','')
                if ticket or storage.config_status()['enabled']:
                    owner,_,_=await run_in_threadpool(job_queue.identity,request)
                    if not ticket:raise HTTPException(428,'Zuerst einen Warteschlangenplatz anfordern.')
                    # Verify before touching the upload body; hold compute admission
                    # through parsing, computation and result handling.
                    if not COMPUTE_LOCK.acquire(blocking=False):raise HTTPException(429,'Eine Berechnung läuft bereits.')
                    compute_reserved=True
                    job_queue.QUEUE.claim(ticket,owner,path.rsplit('/',1)[-1]);claimed=ticket
                    scope.setdefault('state',{})['queue_claimed']=True
                elif job_queue.QUEUE.counts()['active']:
                    raise HTTPException(429,'Bitte einen Warteschlangenplatz anfordern.')
            if not control:
                if not self.upload_lock.acquire(blocking=False):raise HTTPException(429,'Ein Upload wird bereits verarbeitet. Bitte erneut versuchen.')
                upload_reserved=True
            return await self.handle_post(scope,receive,send,limit)
        except (HTTPException,storage.StorageError) as exc:
            detail=exc.detail if isinstance(exc,HTTPException) else str(exc)
            return await JSONResponse({'detail':detail},status_code=exc.status_code)(scope,receive,send)
        finally:
            if upload_reserved:self.upload_lock.release()
            if compute_reserved:COMPUTE_LOCK.release()
            if claimed:job_queue.QUEUE.finish(claimed)

    async def handle_post(self,scope,receive,send,limit=MAX_BYTES):
        chunks=[];size=0
        while True:
            try:message=await asyncio.wait_for(receive(),timeout=UPLOAD_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                raise HTTPException(408,'Upload wegen Zeitüberschreitung beendet. Bitte erneut starten.') from None
            if message['type']=='http.disconnect':return
            body=message.get('body',b'');size+=len(body)
            if size>limit:
                return await JSONResponse({'detail':'Anfrage zu groß.'},status_code=413)(scope,receive,send)
            chunks.append(body)
            if not message.get('more_body',False):break
        replayed=False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        return await self.app(scope,replay,send)


app=FastAPI(title='Dose Atlas research prototype',docs_url=None,redoc_url=None)
app.add_middleware(SizeLimit)
app.include_router(cloud_api.router)
app.include_router(job_queue.router)
app.include_router(auth_activation.router)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


@app.exception_handler(InputError)
async def input_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=422)


@app.exception_handler(storage.StorageError)
async def storage_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=exc.status_code)


@app.middleware('http')
async def privacy_headers(request,call_next):
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    return response


@app.get('/')
def index():return FileResponse(ROOT/'static'/'index.html')


@app.get('/api/health')
def health():return {'status':'ok','service':'dose-atlas','model_ready':(ROOT/'artifacts'/'model.joblib').exists()}


@app.get('/api/method')
def method():return FileResponse(ROOT/'docs'/'OPEN_METHOD.md',media_type='text/plain; charset=utf-8')


@app.get('/api/source')
def source():
    # Explicit public code folders only; never include trained weights, caches or .env.
    files=[ROOT/n for n in ('app.py','job_queue.py','auth_activation.py','cloud_api.py','storage.py','dose_export.py','geometry.py','model.py',
          'metrics.py','upload.py','local_cases.py','train.py','check_resolution.py','verify_real.py',
          'verify_uploads.py','verify_hosted.py','evaluate_continuity.py','build_package.py','README.md','LICENSE','Dockerfile',
          '.dockerignore','.gitignore','requirements.txt','requirements-runtime.txt','requirements-dev.txt','start.ps1',
          'dose_calibration.py','overview_model.py','validate_calibration.py','test_overview_model.py',
          'CITATION.cff','SECURITY.md','CHANGELOG.md','artifacts/README.md',
          'docs/assets/dose-atlas-banner.png','docs/assets/method-principle.png','docs/assets/PROVENANCE.md')]
    for folder,pattern in (('static','**/*'),('tests','*.py'),('tests','*.cjs'),('docs','*.md'),('deploy','*.sql'),('deploy','*.py')):
        files.extend((ROOT/folder).glob(pattern))
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_file():archive.write(path,'dose-atlas/'+path.relative_to(ROOT).as_posix())
    return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="Dose-Atlas-source-MIT.zip"'})


@lru_cache(maxsize=1)
def load_model():
    path=ROOT/'artifacts'/'model.joblib'
    if not path.exists():raise InputError('Model weights not installed. See docs/MODELS.md. Only the synthetic analytic demo is available.')
    return joblib.load(path)


@app.get('/api/model')
def model_card():
    path=ROOT/'artifacts'/'model_card.json'
    if not path.exists() or not (ROOT/'artifacts/model.joblib').exists():
        return {'name':'Dose Atlas','status':'Model weights not installed','training_cases':0,
                'limitations':['Source-only installation. Synthetic analytic demo is not a trained prediction. See docs/MODELS.md.']}
    card=json.loads(path.read_text(encoding='utf-8'))
    resolution=ROOT/'artifacts'/'resolution_check.json'
    if resolution.exists():card['resolution_check']=json.loads(resolution.read_text())['summary']
    continuity=ROOT/'artifacts'/'continuity_validation.json'
    if continuity.exists():card['spatial_regularization']=json.loads(continuity.read_text(encoding='utf-8'))
    if (ROOT/'artifacts/dose_calibration.json').exists():card['dose_calibration']=calibration_card()
    return card


def locked_job(function,*args):
    if not job_queue.QUEUE.acquire_unqueued(COMPUTE_LOCK):
        raise HTTPException(429,'Eine Berechnung läuft bereits. Bitte danach erneut starten.')
    try:return safe_job(function,*args)
    finally:job_queue.QUEUE.release_unqueued(COMPUTE_LOCK)


def safe_job(function,*args):
    try:return function(*args)
    except (InputError,HTTPException):raise
    except Exception as exc:
        # Do not return filenames, identifiers or parser exception strings.
        raise InputError('Eingabe konnte nicht sicher verarbeitet werden. DICOM-Geometrie und Auswahl prüfen.') from None


async def contents(upload):
    if upload is None:return None
    try:
        data=await upload.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:raise InputError('Datei zu groß.')
        return data
    finally:await upload.close()


def inspect_structure(data,dose_data=None,anonymize=True):
    return inspect_upload(data,dose_data,anonymize)


@app.post('/api/inspect')
async def inspect(request:Request,structure:UploadFile=File(...),dose:UploadFile|None=File(None),anonymize:bool=Form(True)):
    cloud_api.same_origin(request)
    await run_in_threadpool(cloud_api.require_account,request)
    if storage.config_status()['enabled'] and not anonymize:raise HTTPException(422,'Die gehostete Ablage erfordert aktivierte Anonymisierung.')
    return await run_in_threadpool(safe_job if getattr(request.state,'queue_claimed',False) else locked_job,inspect_structure,await contents(structure),await contents(dose),anonymize)


def response_data(rois,targets,rx,grid,X,labels,stats,pred,ref=None,extra_warnings=None,demo=False,prescriptions=None,regularization_sigma_mm=1.,calibration_scale=1.):
    prescriptions=prescriptions or [rx]*len(targets)
    mixed=len(set(prescriptions))>1
    warnings=list(extra_warnings or [])
    if calibration_scale != 1.:
        warnings.append('Empirische Dosiskalibrierung; keine erreichbare Optimaldosis. Faktor gilt für Dosisfeld, DVH, Kennzahlen und Export, nicht für die Referenz.')
    if regularization_sigma_mm:
        warnings.append('Vorhersage räumlich regularisiert (σ=1 mm); DVH, Kennzahlen und Export verwenden dieselbe Dosis. Referenzdosis unverändert.')
    if any(r.get('source_planes') is not None for r in rois.values()):
        warnings.append('HDSS-Quellebenen berücksichtigt; Auswertung auf 1 mm, keine native HDSS-DVH-Validierung.')
    domain=(X[:,0]<=35).reshape(labels.shape)
    if ref is not None:
        if not np.isfinite(ref[labels>0]).all():
            raise InputError('Referenzdosis deckt nicht alle Ziele ab. Vergleich wird abgebrochen.')
        common=domain&np.isfinite(ref)
        coverage=100*common.sum()/domain.sum()
        if coverage<99.999:warnings.append(f'Referenz deckt {coverage:.1f}% der 35-mm-Domäne ab; globale Vergleichswerte nutzen nur gemeinsame Unterstützung.')
    else:common=domain;coverage=None
    local_rows, local_means = local_target_metrics(pred,ref,labels,domain,prescriptions,grid.spacing)
    metrics=[];dvhs=[]
    for k,n in enumerate(targets,1):
        mask=labels==k
        name=rois[n]['name'];target_rx=prescriptions[k-1]
        pm=dose_metrics(pred[mask],target_rx);rm=dose_metrics(ref[mask],target_rx) if ref is not None else None
        pm.update(local_rows[k-1]['predicted'])
        if rm is not None: rm.update(local_rows[k-1]['reference'])
        metrics.append(dict(local_coverage_pct=local_rows[k-1]['local_coverage_pct'],local_region_cc=local_rows[k-1]['local_region_cc'],roi=name,number=n,roi_number=n,prescription_Gy=target_rx,role='target',volume_cc=float(mask.sum()*grid.voxel_cc),predicted=pm,reference=rm))
        dvhs.append(dict(roi=name,number=n,roi_number=n,prescription_Gy=target_rx,predicted=dvh(pred[mask],target_rx),reference=dvh(ref[mask],target_rx) if ref is not None else None))
    # Evaluate a small explicitly recognizable set of organs; no inferred constraints.
    oar_terms=('brainstem','hirnstamm','chiasm','optic','sehnerv','brain','hirn')
    for n,r in rois.items():
        if n in targets or not any(t in r['name'].lower() for t in oar_terms):continue
        if r.get('frame') != rois[targets[0]].get('frame'):
            warnings.append('Eine Organstruktur liegt in einem anderen Referenzrahmen; kein Organ-DVH berechnet.')
            continue
        try:mask=rasterize(r,grid)
        except InputError:
            warnings.append('Eine Organstruktur besitzt nicht unterstützte Schichtgeometrie; keine Organmetrik berechnet.');continue
        if not mask.any():continue
        low_roi,high_roi=roi_bounds(r)
        # Conservative: require full contours inside volume and mask inside domain.
        high=grid.origin+(np.asarray(grid.shape[::-1])-1)*grid.spacing
        complete=bool(np.all(low_roi>=grid.origin-grid.spacing/2)&np.all(high_roi<=high+grid.spacing/2)&np.all(domain[mask]))
        if not complete or (ref is not None and not np.isfinite(ref[mask]).all()):
            warnings.append(f'{r["name"]}: außerhalb der vollständigen Auswertungsdomäne; kein irreführendes Teil-DVH.')
            continue
        metrics.append(dict(roi=r['name'],prescription_Gy=rx,role='organ',volume_cc=float(mask.sum()*grid.voxel_cc),
                            predicted=dose_metrics(pred[mask],rx),reference=dose_metrics(ref[mask],rx) if ref is not None else None))
        dvhs.append(dict(roi=r['name'],prescription_Gy=rx,predicted=dvh(pred[mask],rx),reference=dvh(ref[mask],rx) if ref is not None else None))
    if mixed:
        for row in metrics:
            if row['role']=='organ':
                row['prescription_Gy']=None
                for key in ('predicted','reference'):
                    if row[key] is not None:row[key]['V100_pct']=None
        target_names={rois[n]['name'] for n in targets}
        for row in dvhs:
            if row['roi'] not in target_names:row['prescription_Gy']=None
    card=model_card()
    learned=card.get("prescription_range_Gy",[18,20])
    if any(not learned[0]<=v<=learned[1] for v in prescriptions):
        warnings.append("Verschreibung außerhalb des gelernten Bereichs 18–20 Gy: unvalidierte Extrapolation.")
    if mixed:
        warnings.append("Unterschiedliche Target-Verschreibungen: explorative distanzgewichtete Skalierung; nicht mit heterogenen Verschreibungen trainiert oder validiert. Globaler CI/GI nicht definiert.")
    warnings+=['Schätzung eines Referenz-Workflows; keine erreichbare Optimaldosis und kein ausführbarer Bestrahlungsplan.',
               'OAR-Schonung ist kein gelerntes Modellziel. Organmetriken sind rein beschreibend.',
               '1-mm-Geometrieraster; kleine Strukturen können gegenüber nativen TPS-DVHs abweichen.',
               'V12/CI/GI beziehen sich auf die begrenzte Zielumgebung, nicht automatisch auf das gesamte Gehirn.']
    limits=card.get('target_volume_range_cc',[0,1e9]);counts=card.get('target_count_range',[1,40])
    if any(s['volume_cc']<limits[0] or s['volume_cc']>limits[1] for s in stats) or not counts[0]<=len(targets)<=counts[1]:
        warnings.append('Außerhalb des beobachteten Größen-/Zielzahlbereichs: Ergebnis ist eine unvalidierte Extrapolation.')
    # Display planes plus each target centroid plane; quantitative grid unchanged.
    centres=[int(np.clip(round((s['center'][2]-grid.origin[2])/grid.spacing),0,grid.shape[0]-1))
             for s in stats if 'center' in s]
    zi=np.unique(np.r_[np.linspace(0,grid.shape[0]-1,min(96,grid.shape[0])).astype(int),centres]).astype(int)
    stride=max(1,int(np.ceil(max(grid.shape[1:])/180)))
    sl=(zi,slice(None,None,stride),slice(None,None,stride))
    displayed=np.where(domain,pred,np.nan)[sl]
    def serialize(a):
        # Null marks unsupported pixels, not zero dose.
        return np.where(np.isfinite(a),np.round(a,3),None).tolist()
    ref_view=ref[sl] if ref is not None else None
    summary=dict(prediction_calibration_scale=calibration_scale,prediction_calibration_version=1 if calibration_scale != 1. else 0,prediction_regularization_sigma_mm=regularization_sigma_mm,prescription_Gy=rx,prescription_range_Gy=[min(prescriptions),max(prescriptions)],
                 target_prescriptions={str(n):v for n,v in zip(targets,prescriptions)},mixed_prescriptions=mixed,fractions=1,target_count=len(targets),grid_mm=grid.spacing,
                 domain='35-mm target neighbourhood',reference_coverage_pct=coverage,synthetic_demo=demo,
                 predicted=global_metrics(pred,labels,common,prescriptions[0],grid.voxel_cc,mixed_prescriptions=mixed),
                 reference=global_metrics(ref,labels,common,prescriptions[0],grid.voxel_cc,mixed_prescriptions=mixed) if ref is not None else None,
                 max_display_Gy=float(max(2*max(prescriptions),np.nanmax(pred),np.nanmax(ref) if ref is not None else 0)))
    summary['local_metrics_version']=1
    for kind in ('predicted','reference'):
        if summary[kind] is not None:
            summary[kind].update(local_means[kind])
            ci=summary[kind].get('Paddick_CI')
            summary[kind]['inverse_CI']=1/ci if ci else None
    targets3d=[]
    for n,s in zip(targets,stats):
        points=np.concatenate(rois[n]['contours'])
        all_contours=rois[n]['contours']
        display_contours=[display_polyline(c).round(4).tolist() for c in all_contours]
        targets3d.append(dict(number=n,prescription_Gy=prescriptions[targets.index(n)],name=rois[n]['name'],center_lps_mm=s.get('center',points.mean(0).tolist()),
                              radius_mm=s.get('radius_mm',1),bounds_min_lps_mm=points.min(0).tolist(),
                              bounds_max_lps_mm=points.max(0).tolist(),contours=display_contours))
    display_paths,display_step=display_target_contours(rois,targets,grid.axes[2][zi])
    rings=ring_benchmark(pred,ref,labels,domain,rx,grid.spacing,[rois[n]['name'] for n in targets],prescriptions=prescriptions)
    return dict(summary=summary,warnings=warnings,metrics=metrics,dvhs=dvhs,targets3d=targets3d,ring_benchmark=rings,
                slices=dict(isodose_paths={'predicted':display_isodoses(pred,grid,zi,domain),'reference':display_isodoses(ref,grid,zi,domain)},isodose_grid_mm=grid.spacing,target_contours=display_paths,contour_sampling_mm=display_step,shape=list(displayed.shape),spacing_mm=grid.spacing*stride,indices=zi.tolist(),
                            predicted=serialize(displayed),reference=serialize(ref_view) if ref_view is not None else None,
                            target=(labels[sl]>0).astype(int).tolist(),z_mm=grid.axes[2][zi].tolist(),origin_lps_mm=grid.origin.tolist()),
                model={'name':card['name'],'status':card['status']})


def compute(structure_data,dose_data,targets_text,rx,fractions,anonymize=True,with_bundle=False,target_prescriptions=None,regularization_sigma_mm=1.,calibrated=False):
    started=time.perf_counter()
    if type(regularization_sigma_mm) not in (int,float) or regularization_sigma_mm not in (0.,1.):
        raise InputError('Vorhersagevariante muss Basismodell oder Regularisierung mit 1 mm sein.')
    if not np.isfinite(rx) or fractions!=1:
        raise InputError('Nur kranielle Einzeit-SRS mit endlicher Verschreibung wird unterstützt.')
    factor=calibration_factor(calibrated,sigma_mm=regularization_sigma_mm)
    card=model_card()
    try:
        targets=json.loads(targets_text)
        if not isinstance(targets,list) or any(type(n)!=int for n in targets) or len(set(targets))!=len(targets):raise ValueError()
    except (ValueError,TypeError):raise InputError('Zielauswahl muss eine Liste eindeutiger ROI-Nummern sein.')
    try:
        overrides=json.loads(target_prescriptions) if isinstance(target_prescriptions,str) else target_prescriptions
    except (ValueError,TypeError):raise InputError('Ungültige Target-Verschreibungen.')
    prescriptions=prescription_values(targets,rx,overrides)
    ds,dose_ds,privacy=prepare_upload(structure_data,dose_data,anonymize);rois=structure_rois(ds)
    grid=make_grid(rois,targets)
    frame=structure_frame(ds,targets)
    X,labels,stats=geometry_features(rois,targets,grid)
    pred=scale_prescriptions(regularize_prediction(predict(load_model(),X).reshape(grid.shape),grid.spacing,sigma_mm=regularization_sigma_mm)*factor,labels,prescriptions,grid.spacing)
    ref=sample_dose(dose_ds,frame,grid) if dose_ds is not None else None
    result=response_data(rois,targets,rx,grid,X,labels,stats,pred,ref,[privacy['message']],prescriptions=prescriptions,regularization_sigma_mm=regularization_sigma_mm,calibration_scale=factor)
    result['anonymization']=privacy
    result['elapsed_seconds']=round(time.perf_counter()-started,2)
    if with_bundle:
        # Pair follows the selected anonymization mode. No original UID mapping is retained.
        if not ds.get('StudyInstanceUID'):
            from pydicom.uid import generate_uid
            ds.StudyInstanceUID=generate_uid()
        bundle={'structure.dcm':export_structure(ds),'prediction.dcm':export_prediction(pred,grid,ds,targets,rx,target_prescriptions=prescriptions,regularization_sigma_mm=regularization_sigma_mm,calibration_scale=factor),
                'model_card.json':json.dumps(card).encode('utf-8')}
        result['warnings'].append('RTDOSE-Download enthält das vollständige Modellraster; außerhalb 35 mm unvalidierte Extrapolation. RTSTRUCT und RTDOSE gemeinsam importieren; TPS-Import noch nicht nativ geprüft.')
        return result,bundle
    return result


@app.post('/api/predict')
async def inference(request:Request,structure:UploadFile=File(...),dose:UploadFile|None=File(None),
                    targets:str=Form(...),prescription:float=Form(...),fractions:int=Form(1),anonymize:bool=Form(True),target_prescriptions:str|None=Form(None),regularization_sigma_mm:float=Form(1.),calibrated:bool=Form(False)):
    cloud_api.same_origin(request)
    await run_in_threadpool(cloud_api.require_account,request)
    if storage.config_status()['enabled'] and not anonymize:raise HTTPException(422,'Die gehostete Ablage erfordert aktivierte Anonymisierung.')
    structure_data=await contents(structure);dose_data=await contents(dose)
    result,bundle=await run_in_threadpool(safe_job if getattr(request.state,'queue_claimed',False) else locked_job,compute,structure_data,dose_data,targets,prescription,fractions,anonymize,True,target_prescriptions,regularization_sigma_mm,calibrated)
    return await run_in_threadpool(cloud_api.finish_result,result,bundle,request)


def demo_result():
    started=time.perf_counter();rois={}
    centers=[(-19,-12,-6),(14,11,8),(21,-19,3),(-14,22,17),(5,2,-20),(2,27,-14)]
    for n,(center,radius) in enumerate(zip(centers,[5,7,4,6,4,5]),1):
        c=np.asarray(center);contours=[]
        for z in np.arange(c[2]-radius+.5,c[2]+radius,1):
            rr=np.sqrt(max(0,radius**2-(z-c[2])**2));a=np.linspace(0,2*np.pi,72,endpoint=False)
            contours.append(np.column_stack((c[0]+rr*np.cos(a),c[1]+rr*np.sin(a),np.full_like(a,z))))
        rois[n]=dict(number=n,name=f'Demo PTV {n:02}',contours=contours)
    targets=list(rois);grid=make_grid(rois,targets);X,labels,stats=geometry_features(rois,targets,grid)
    trained=(ROOT/'artifacts/model.joblib').is_file()
    relative=predict(load_model(),X) if trained else baseline(X).astype(np.float32)
    pred=regularize_prediction(relative.reshape(grid.shape),grid.spacing)*20
    # Deliberately different analytic reference solely to exercise comparison UI.
    ref=(baseline(X)*20).reshape(grid.shape)
    result=response_data(rois,targets,20,grid,X,labels,stats,pred,ref,
                         ['SYNTHETISCHE DEMO: künstliche Kugelgeometrie und analytische Vergleichsdosis; kein Testpatient.'],True)
    result['summary']['prediction_source']='trained_model_synthetic_geometry' if trained else 'analytic_demo_not_trained'
    if not trained:
        result['warnings'].insert(0,'ANALYTIC DEMO — NOT a trained prediction. No model weights installed. Both fields are synthetic analytic illustrations, not validation evidence.')
        result['model']={'name':'Analytic synthetic demo (not HGB)','status':'No trained model installed'}
    result['elapsed_seconds']=round(time.perf_counter()-started,2)
    return result


@app.get('/api/demo')
async def demo():return await run_in_threadpool(locked_job,demo_result)


def local_access(request):
    return local_cases.enabled() and request.client is not None and request.client.host in ('127.0.0.1','::1','testclient')


@app.get('/api/test-cases')
def test_cases(request:Request):
    return {'cases':[{'id':f'C{i:02}','label':f'Testplan C{i:02}'} for i in range(1,11)] if local_access(request) else []}


def real_case_result(case):
    started=time.perf_counter();paths=local_cases.case_paths(case)
    meta=json.loads((ROOT/'private'/f'{case}.json').read_text())
    data={k:p.read_bytes() for k,p in paths.items()}
    if any(hashlib.sha256(data[k]).hexdigest()!=v for k,v in meta['source_hashes'].items()):
        raise InputError('Testfalldaten seit Validierung verändert; erneute Aufbereitung erforderlich.')
    inspection=inspect_upload(data['structure'],data['dose'],True)
    ds,dose,privacy=prepare_upload(data['structure'],data['dose'],True)
    rois=structure_rois(ds)
    targets=[s['number'] for s in meta['stats']]
    with np.load(ROOT/'private'/f'{case}.npz') as cache:X,labels=cache['X'],cache['labels']
    grid=Grid(np.asarray(meta['origin']),tuple(meta['shape']),meta['spacing_mm'])
    factor=calibration_factor(True,test_case=case)
    with np.load(ROOT/'private'/f'{case}_heldout.npz') as cache:pred=regularize_prediction(cache['pred'].reshape(grid.shape),grid.spacing)*meta['rx_Gy']*factor
    ref=sample_dose(dose,structure_frame(ds,targets),grid)
    result=response_data(rois,targets,meta['rx_Gy'],grid,X,labels,meta['stats'],pred,ref,
                         [f'ECHTER TESTPLAN {case}: Referenzdose aus DICOM, Vorhersage aus dem Fold ohne diesen Patienten.',
                          privacy['message'],'Die hier verwendete Verschreibung stammt aus RTPLAN; D98-Vorschläge daneben sind eigenständige Vermutungen.'],calibration_scale=factor)
    result['summary']['real_test_case']=case
    result['anonymization']=privacy
    result['elapsed_seconds']=round(time.perf_counter()-started,2)
    inspection.update(result=result,case=case,source='real_test_plan',planned_prescription_Gy=meta['rx_Gy'])
    return inspection


@app.get('/api/test-cases/{case}')
async def real_case(case:str,request:Request):
    if not local_access(request):raise HTTPException(404,'Lokale Testfälle sind hier deaktiviert.')
    return await run_in_threadpool(locked_job,real_case_result,case)
