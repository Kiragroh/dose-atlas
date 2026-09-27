"""Versioned empirical scalar calibration, independent of uploaded reference dose."""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
from geometry import InputError

ROOT = Path(__file__).resolve().parent

@lru_cache(maxsize=1)
def calibration_card():
    card = json.loads((ROOT/'artifacts/dose_calibration.json').read_text(encoding='utf-8'))
    if hashlib.sha256((ROOT/'artifacts/model.joblib').read_bytes()).hexdigest() != card['model_sha256']:
        raise InputError('Kalibrierung passt nicht zum Modell; Modell und Kalibrierung gemeinsam aktualisieren.')
    if card['version'] != 1 or not 1 <= card['scale'] <= 1.08:
        raise InputError('Ungültige Modellkalibrierung.')
    return card

def calibration_factor(enabled=False, *, sigma_mm=1., test_case=None):
    if type(enabled) is not bool:
        raise InputError('Kalibrierung muss ein boolescher Wert sein.')
    if not enabled:
        return 1.
    if sigma_mm != 1.:
        raise InputError('Kalibrierung ist nur für die regularisierte 1-mm-Variante verfügbar.')
    card = calibration_card()
    return float(card['fold_scales'][test_case] if test_case else card['scale'])


def publish_validation(report):
    """Publish only cohort summaries and fixed parameters, never voxel data."""
    import numpy as np
    ref=np.concatenate([f['reference'] for f in report['folds']])
    fields={'before':np.concatenate([f['before'] for f in report['folds']]),
            'after':np.concatenate([f['after'] for f in report['folds']]),'reference':ref}
    validation={}
    for key,a in fields.items():
        validation[key]={'inverse_CI':float(a[:,0].mean()),'GI':float(a[:,1].mean()),
            'below_1_1':int((a[:,0]<1.1).sum()),'D98_MAE_Gy':float(abs(a[:,3]-ref[:,3]).mean()),
            'V12_MAE_cc':float(abs(a[:,2]-ref[:,2]).mean()),'Dmean_MAE_Gy':float(abs(a[:,5]-ref[:,5]).mean()),
            'coverage_MAE_percentage_points':float(abs(a[:,4]-ref[:,4]).mean()*100),
            'CI_MAE':float(abs(a[:,0]-ref[:,0]).mean()),'GI_MAE':float(abs(a[:,1]-ref[:,1]).mean())}
    case_mae={}
    for key in ['before','after']:
        e=np.mean([np.mean(abs(np.array(f[key])-np.array(f['reference'])),axis=0) for f in report['folds']],axis=0)
        case_mae[key]=dict(zip(['inverse_CI','GI','V12_cc','D98_Gy','coverage_fraction','Dmean_Gy'],e.tolist()))
    card=dict(version=1,name='Empirical dose-level calibration v1',scale=report['deployment_selection']['scale'],
        sigma_mm=1.,model_sha256=report['model_sha256'],cases=len(report['folds']),targets=len(ref),
        method=report['method'],deployment_fit='Scalar selected from all development-case LOPO fields; base HGB trained on all ten cases.',
        constraints='On inner calibration cases: D98 case-macro MAE increase <=0.10 Gy, GI/V12/coverage MAE no worse. Fixed candidate scale grid 1.00..1.08 step0.01. These are development selection rules, not clinical tolerances.',
        validation=validation,case_macro_MAE=case_mae,
        fold_scales={f['case']:f['selection']['scale'] for f in report['folds']},
        limitations=['Exploratory development series after identifying bias; no independent external validation.',
            'Scalar changes the whole predicted field; no CI floor or fitting to the uploaded reference.',
            'Conformity optimism remains; not a deliverable optimum or proof of inferior reference planning.',
            'Individual D98 errors may increase; heterogeneous prescriptions and 24-target cases are extrapolations.'])
    (ROOT/'artifacts/dose_calibration.json').write_text(json.dumps(card,indent=2),encoding='utf-8')
    return card
