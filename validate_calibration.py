"""Outer patient-held-out calibration: no held-out data in fit/parameter choice.
Development analysis after observing cohort limitations; not external validation.
"""
import os
os.environ['OMP_NUM_THREADS']='4';os.environ['OPENBLAS_NUM_THREADS']='4'
import sys,json,time,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
import numpy as np,joblib
from scipy.ndimage import distance_transform_edt
from model import new_regressor,predict,regularize_prediction
def prepare_case(cid):
 meta=json.loads((ROOT/'private'/f'{cid}.json').read_text())
 with np.load(ROOT/'private'/f'{cid}.npz') as a:
  lab=a['labels'];ref=a['y'].reshape(lab.shape);X=a['X']
 dom=(X[:,0]<=35).reshape(lab.shape)
 idx=distance_transform_edt(lab==0,return_distances=False,return_indices=True)
 owner=lab[tuple(idx)];del idx
 territories=[]
 for k in range(1,int(lab.max())+1):
  mask=(owner==k)&dom
  if not np.isfinite(ref[mask]).all():continue
  territories.append((np.flatnonzero(mask),np.flatnonzero(lab==k),np.flatnonzero(mask&(lab==0))))
 return meta,X,lab,ref,territories

def rows(dose,territories,rx,scale=1.):
 flat=dose.ravel();out=[]
 for ti,ta,ou in territories:
  p=np.sort(flat[ti]);t=np.sort(flat[ta]);o=np.sort(flat[ou]);n=len(t)
  v=n-np.searchsorted(t,1/scale);piv=len(p)-np.searchsorted(p,1/scale);half=len(p)-np.searchsorted(p,.5/scale)
  out.append([n*piv/v**2 if v else 100.,half/piv if piv else 100.,(len(o)-np.searchsorted(o,12/rx/scale))*.001,float(np.percentile(t,2)*scale*rx),v/n,float(t.mean()*scale*rx)])
 return np.array(out)

def loss(a,b):
 # Equal targets within equal cases. Dimensionless errors with fixed scales.
 return float(np.mean(np.abs(a[:,0]-b[:,0])/.15)+np.mean(np.abs(a[:,1]-b[:,1])/np.maximum(b[:,1],1))/.25+np.mean(np.abs(a[:,2]-b[:,2])/np.maximum(b[:,2],.5))/.25+np.mean(np.abs(a[:,3]-b[:,3]))+np.mean(np.abs(a[:,4]-b[:,4]))/.05+np.mean(np.abs(a[:,5]-b[:,5]))/1.)


SCALES=[1.,1.01,1.02,1.03,1.04,1.05,1.06,1.07,1.08]

def evaluate(m,cid):
 meta,X,lab,ref,territories=prepare_case(cid)
 field=regularize_prediction(predict(m,X).reshape(lab.shape),meta['spacing_mm']);del X
 truth=rows(ref,territories,meta['rx_Gy']);outs=[rows(field,territories,meta['rx_Gy'],scale) for scale in SCALES]
 return {'case':cid,'reference':truth.tolist(),'candidates':[a.tolist() for a in outs]}

def errors(record):
 b=np.array(record['reference']);outs=[np.array(a) for a in record['candidates']]
 return np.array([np.mean(abs(a-b),axis=0) for a in outs]),np.array([loss(a,b) for a in outs])

def select(records):
 es,ls=zip(*(errors(r) for r in records));e=np.mean(es,axis=0);l=np.mean(ls,axis=0)
 # D98 <= +0.10 Gy mean absolute error; GI/V12/coverage no worse in fitting cases.
 allowed=(e[:,3]<=e[0,3]+.10)&(e[:,1]<=e[0,1]+1e-9)&(e[:,2]<=e[0,2]+1e-9)&(e[:,4]<=e[0,4]+1e-9)
 idx=int(np.argmin(np.where(allowed,l,np.inf)))
 return idx,{'scale':SCALES[idx],'candidate_case_macro_mae':e.tolist(),'objective':l.tolist(),'eligible':allowed.tolist()}

if __name__=='__main__':
 cids=[f'C{i:02}' for i in range(1,11)];samples=[]
 for cid in cids:
  with np.load(ROOT/'private'/f'{cid}.npz') as a:samples.append((a['train_X'],a['train_y']))
 report={'method':'Outer leave-one-patient-out: HGB and scalar calibration fit on nine training cases only; Three inner case folds (6 training, 3 calibration patients) produce calibration fields. No outer test patient enters any inner fit or selection. Test reference is evaluated only after selection. Exploratory development cohort; not external validation.','scales':SCALES,'folds':[]}
 for i,cid in enumerate(cids):
  started=time.time();m=new_regressor().fit(np.concatenate([x for j,(x,y) in enumerate(samples) if j!=i]),np.concatenate([y for j,(x,y) in enumerate(samples) if j!=i]))
  fitted=[]
  outer_indices=[j for j in range(10) if j!=i]
  for g in range(3):
   valid=outer_indices[g::3];train=[j for j in outer_indices if j not in valid]
   inner=new_regressor().fit(np.concatenate([samples[j][0] for j in train]),np.concatenate([samples[j][1] for j in train]))
   fitted.extend(evaluate(inner,cids[j]) for j in valid)
  idx,selection=select(fitted)
  test=evaluate(m,cid)
  report['folds'].append(dict(case=cid,training_cases=[c for c in cids if c!=cid],selection=selection,reference=test['reference'],before=test['candidates'][0],after=test['candidates'][idx]))
  (ROOT/'private/scalar_calibration_validation.json').write_text(json.dumps(report))
  print(cid,'selected',SCALES[idx],'CI',round(np.mean(np.array(test['candidates'][idx])[:,0]),3),'seconds',round(time.time()-started,1),flush=True)
 # Final scalar uses existing true LOPO predictions across development cases.
 fitted=[]
 for cid in cids:
  meta,X,lab,ref,territories=prepare_case(cid);del X
  with np.load(ROOT/'private'/f'{cid}_heldout.npz') as a:field=regularize_prediction(a['pred'].reshape(lab.shape),meta['spacing_mm'])
  truth=rows(ref,territories,meta['rx_Gy'])
  fitted.append({'case':cid,'reference':truth.tolist(),'candidates':[rows(field,territories,meta['rx_Gy'],v).tolist() for v in SCALES]})
 idx,selection=select(fitted);report['deployment_selection']=selection
 report['model_sha256']=hashlib.sha256((ROOT/'artifacts/model.joblib').read_bytes()).hexdigest()
 (ROOT/'private/scalar_calibration_validation.json').write_text(json.dumps(report,indent=2))
 from dose_calibration import publish_validation
 publish_validation(report)
 print('FINAL',SCALES[idx],flush=True)
