"""Read-only ten-case preparation and fixed exploratory patient-held-out validation."""
import os
os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '4')
import argparse
import json
import hashlib
import platform
import time
from pathlib import Path
import joblib
import numpy as np
import pydicom
import sklearn
from sklearn.isotonic import IsotonicRegression
from geometry import read_dicom, structure_rois, structure_frame, make_grid, sample_dose, InputError
from model import geometry_features, new_regressor, sample_training, predict, baseline, FEATURE_NAMES
from metrics import dose_metrics, global_metrics

ROOT = Path(__file__).resolve().parent


def prepare(inventory,fresh=False):
    rows = json.loads(Path(inventory).read_text(encoding='utf-8'))
    patients = sorted({r['patient_id'] for r in rows})
    output = []
    for i, patient in enumerate(patients,1):
        cid = f'C{i:02}'
        cache = ROOT/'private'/f'{cid}.npz'
        meta_path = ROOT/'private'/f'{cid}.json'
        if not fresh and cache.exists() and meta_path.exists():
            output.append(json.loads(meta_path.read_text()))
            print(cid, 'cache ready', flush=True)
            continue
        rr = [r for r in rows if r['patient_id']==patient and r['stage']=='Elements']
        doses = [r for r in rr if r['modality']=='RTDOSE' and r['summation']=='PLAN']
        if len(doses) != 1:
            raise InputError(f'{cid}: Eindeutige Referenzdosis fehlt.')
        dr = doses[0]
        plans = [r for r in rr if r['modality']=='RTPLAN' and r['sop'] in dr['plan_refs']]
        if len(plans) != 1:
            raise InputError(f'{cid}: Planreferenz uneindeutig.')
        pr = plans[0]
        structures = [r for r in rr if r['modality']=='RTSTRUCT' and r['sop'] in pr['structure_refs']]
        if len(structures) != 1:
            raise InputError(f'{cid}: Strukturreferenz uneindeutig.')
        plan = read_dicom(pr['path'],'RTPLAN')
        ds = read_dicom(structures[0]['path'],'RTSTRUCT')
        dose = read_dicom(dr['path'],'RTDOSE')
        fractions = [int(x.NumberOfFractionsPlanned) for x in plan.FractionGroupSequence]
        if fractions != [1]:
            raise InputError(f'{cid}: Nur eine Fraktion wird unterstützt.')
        prescribed = [r for r in plan.DoseReferenceSequence if str(r.DoseReferenceType)=='TARGET'
                      and hasattr(r,'ReferencedROINumber') and hasattr(r,'TargetPrescriptionDose')]
        rx_values = {float(r.TargetPrescriptionDose) for r in prescribed}
        if len(rx_values)!=1:
            raise InputError(f'{cid}: Gemischte oder fehlende Verschreibung.')
        rx = rx_values.pop()
        numbers = sorted({int(r.ReferencedROINumber) for r in prescribed})
        rois = structure_rois(ds)
        frame = structure_frame(ds,numbers)
        if str(plan.get('FrameOfReferenceUID','')) != frame:
            raise InputError(f'{cid}: Plan und Strukturen haben unterschiedliche Referenzrahmen.')
        grid = make_grid(rois,numbers)
        start = time.perf_counter()
        X, labels, stats = geometry_features(rois,numbers,grid)
        y = sample_dose(dose,frame,grid).ravel()/rx
        if not np.isfinite(y[labels.ravel()>0]).all():
            raise InputError(f'{cid}: Dosis deckt Zielstrukturen nicht vollständig ab.')
        train_X, train_y = sample_training(X,y,20260920+i)
        np.savez_compressed(cache,X=X,y=y,labels=labels,train_X=train_X,train_y=train_y)
        meta = dict(case=cid,rx_Gy=rx,fractions=1,targets=len(numbers),stats=stats,
                    shape=list(grid.shape),origin=grid.origin.tolist(),spacing_mm=grid.spacing,
                    domain_coverage_pct=float(100*np.isfinite(y[X[:,0]<=35]).mean()),
                    source_hashes={role:hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                   for role,path in [('structure',structures[0]['path']),('plan',pr['path']),('dose',dr['path'])]},
                    seconds=round(time.perf_counter()-start,2))
        meta_path.write_text(json.dumps(meta,indent=2),encoding='utf-8')
        output.append(meta)
        print(cid,'prepared',len(numbers),'targets',len(X),'voxels',meta['seconds'],'s',flush=True)
    return output


def evaluate(cases):
    sampled = []
    for case in cases:
        with np.load(ROOT/'private'/f"{case['case']}.npz") as a:
            sampled.append((a['train_X'],a['train_y']))
    folds=[]
    for i,case in enumerate(cases):
        started=time.perf_counter()
        model = new_regressor()
        train_x=np.concatenate([x for j,(x,y) in enumerate(sampled) if j!=i])
        train_y=np.concatenate([y for j,(x,y) in enumerate(sampled) if j!=i])
        model.fit(train_x,train_y)
        distance_model=IsotonicRegression(increasing=False,out_of_bounds='clip').fit(train_x[:,0],train_y)
        with np.load(ROOT/'private'/f"{case['case']}.npz") as a:
            X,y,labels=a['X'],a['y'],a['labels']
        pred=predict(model,X)
        base=distance_model.predict(X[:,0])
        fixed_base=baseline(X)
        supported=np.isfinite(y)&(X[:,0]<=35)
        rx=case['rx_Gy']
        bands={}
        for name,band in [('target',X[:,0]<0),('0_5mm',(X[:,0]>=0)&(X[:,0]<5)),
                          ('5_15mm',(X[:,0]>=5)&(X[:,0]<15)),('15_35mm',(X[:,0]>=15)&(X[:,0]<=35))]:
            m=supported&band
            bands[name]=dict(MAE_Gy=float(np.mean(abs(pred[m]-y[m]))*rx),
                             baseline_MAE_Gy=float(np.mean(abs(base[m]-y[m]))*rx),
                             fixed_radial_MAE_Gy=float(np.mean(abs(fixed_base[m]-y[m]))*rx),voxels=int(m.sum()))
        targets=[]
        for k in range(1,case['targets']+1):
            m=labels.ravel()==k
            a,b=dose_metrics(pred[m]*rx,rx),dose_metrics(y[m]*rx,rx)
            targets.append(dict(target=k,volume_cc=float(m.sum()/1000),predicted=a,reference=b))
        pglobal=global_metrics((pred*rx).reshape(labels.shape),labels,supported.reshape(labels.shape),rx,.001)
        rglobal=global_metrics((y*rx).reshape(labels.shape),labels,supported.reshape(labels.shape),rx,.001)
        fold=dict(case=case['case'],held_out_patients=1,training_patients=len(cases)-1,
                  targets=case['targets'],rx_Gy=rx,bands=bands,target_metrics=targets,
                  predicted=pglobal,reference=rglobal,seconds=round(time.perf_counter()-started,2))
        folds.append(fold)
        (ROOT/'private'/'lopo_progress.json').write_text(json.dumps(folds,indent=2),encoding='utf-8')
        # Retain held-out voxel prediction privately for repeatable API comparison checks.
        np.savez_compressed(ROOT/'private'/f"{case['case']}_heldout.npz",pred=pred)
        print(case['case'],'LOPO',round(bands['0_5mm']['MAE_Gy'],3),'Gy near-target MAE',fold['seconds'],'s',flush=True)
    all_x=np.concatenate([x for x,y in sampled]);all_y=np.concatenate([y for x,y in sampled])
    final=new_regressor().fit(all_x,all_y)
    joblib.dump(final,ROOT/'artifacts'/'model.joblib')
    aggregates={b:dict(MAE_Gy=float(np.mean([f['bands'][b]['MAE_Gy'] for f in folds])),
                       baseline_MAE_Gy=float(np.mean([f['bands'][b]['baseline_MAE_Gy'] for f in folds])),
                       fixed_radial_MAE_Gy=float(np.mean([f['bands'][b]['fixed_radial_MAE_Gy'] for f in folds])))
                for b in folds[0]['bands']}
    d98=[np.mean([abs(t['predicted']['D98_Gy']-t['reference']['D98_Gy']) for t in f['target_metrics']]) for f in folds]
    v12=[abs(f['predicted']['V12_outside_targets_domain_cc']-f['reference']['V12_outside_targets_domain_cc']) for f in folds]
    card=dict(name='Dose Atlas geometry HGB v0.1',status='Explorative interne LOPO-Validierung; keine klinische Freigabe',
        training_cases=len(cases),training_targets=sum(c['targets'] for c in cases),features=FEATURE_NAMES,
        prescription_range_Gy=[min(c['rx_Gy'] for c in cases),max(c['rx_Gy'] for c in cases)],
        target_count_range=[min(c['targets'] for c in cases),max(c['targets'] for c in cases)],
        target_volume_range_cc=[min(s['volume_cc'] for c in cases for s in c['stats']),max(s['volume_cc'] for c in cases for s in c['stats'])],
        validation=dict(method='Leave-one-patient-out, fixed model, equal samples per case/distance band',
                        baseline='Training-fold-fitted isotonic signed-distance model; fixed radial heuristic also reported',
                        case_macro_MAE=aggregates,target_D98_case_macro_MAE_Gy=float(np.mean(d98)),
                        V12_outside_targets_domain_case_MAE_cc=float(np.mean(v12)),
                        folds=[{k:v for k,v in f.items() if k!='target_metrics'} for f in folds]),
        model_parameters=final.get_params(),grid_mm=1,padding_mm=35,
        limitations=['Zehn retrospektive Fälle eines Referenz-Workflows; keine unabhängige externe Validierung.',
                     'Keine Aussage zur Überlegenheit einer Software oder zur erreichbaren optimalen Dosis.',
                     'Nur kranielle SRS, eine Fraktion und eine gemeinsame Zielverschreibung.',
                     'Kein CT, keine Strahlrichtungen, keine Maschine: keine physikalische Dosisberechnung oder ausführbare Optimierung.',
                     'OAR-Geometrie wird ausgewertet, aber nicht als Modellmerkmal gelernt; OAR-Konflikte sind nicht modelliert.',
                     '1-mm-Raster und Kontur-Slab-Modell: kleine Ziele und native TPS-DVH können deutlich abweichen.',
                     '35-mm-Zielumgebung: V12 ist ein begrenztes Domänenvolumen, kein automatisch vollständiges Hirn-V12.',
                     'Die lokale Fallserie dient der Modellentwicklung; LOPO bleibt explorativ.',
                     'LOPO-Fehler sind deskriptiv und keine kalibrierten individuellen Unsicherheitsintervalle.'],
        runtime=dict(python=platform.python_version(),numpy=np.__version__,sklearn=sklearn.__version__,pydicom=pydicom.__version__),
        source='Ten authorized Elements exports, resolved dose-plan-structure references; source hashes in protected manifest')
    (ROOT/'artifacts'/'model_card.json').write_text(json.dumps(card,indent=2,ensure_ascii=False),encoding='utf-8')
    print('COMPLETE',json.dumps(aggregates),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--inventory',default=str(ROOT.parent/'Paper_HDSS-Analyse'/'private'/'case_inventory.json'))
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--fresh',action='store_true',help='Recompute geometry and source hashes instead of reusing protected caches')
    args=parser.parse_args()
    for d in ['private','artifacts']:(ROOT/d).mkdir(exist_ok=True)
    cases=prepare(args.inventory,args.fresh)
    if not args.prepare_only:evaluate(cases)
