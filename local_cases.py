"""Explicit local-only real test case access. Never included with patient data."""
import json
import re
from pathlib import Path
from geometry import InputError
ROOT=Path(__file__).resolve().parent


def enabled():
    return (ROOT/'private'/'enable_local_test_cases.flag').is_file()


def case_paths(case):
    if not enabled() or not re.fullmatch(r'C(?:0[1-9]|10)',case):
        raise InputError('Lokaler Testfall nicht verfügbar.')
    inventory=ROOT.parent/'Paper_HDSS-Analyse'/'private'/'case_inventory.json'
    rows=json.loads(inventory.read_text(encoding='utf-8'))
    patients=sorted({r['patient_id'] for r in rows})
    patient=patients[int(case[1:])-1]
    rr=[r for r in rows if r['patient_id']==patient and r['stage']=='Elements']
    dose=[r for r in rr if r['modality']=='RTDOSE' and r.get('summation')=='PLAN']
    if len(dose)!=1:raise InputError('Testfall hat keine eindeutige Referenzdosis.')
    plan=[r for r in rr if r['modality']=='RTPLAN' and r['sop'] in dose[0]['plan_refs']]
    if len(plan)!=1:raise InputError('Testfall hat keine eindeutige Planreferenz.')
    structure=[r for r in rr if r['modality']=='RTSTRUCT' and r['sop'] in plan[0]['structure_refs']]
    if len(structure)!=1:raise InputError('Testfall hat keine eindeutige Strukturreferenz.')
    return {k:Path(v['path']) for k,v in [('dose',dose[0]),('plan',plan[0]),('structure',structure[0])]}
