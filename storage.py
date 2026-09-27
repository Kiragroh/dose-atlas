"""Private metadata-only Supabase archive using verified users and RLS tokens.

Strict whitelist schemas exclude binary DICOM, dose pixels, contours, original
names and identifiers. This module never writes local files or uses service keys.
"""
import base64
import json
import math
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import httpx
BUCKET = 'dose-atlas'
TABLE = '/rest/v1/dose_atlas_runs'
MAX_FILE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
ALLOWED_FILES = {'result.json': 'application/json', 'model_card.json': 'application/json'}

class StorageError(ValueError):
    """Safe public message; upstream response bodies are never included."""
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code

def _configuration():
    url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    key = (os.environ.get('SUPABASE_PUBLISHABLE_KEY') or
           os.environ.get('SUPABASE_ANON_KEY') or '').strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise StorageError('Der private Ergebnisspeicher ist nicht konfiguriert.', 503) from None
    valid = (parsed.scheme in ('http', 'https') and parsed.hostname and
             not parsed.username and not parsed.password and
             not parsed.query and not parsed.fragment)
    # Reject privileged credentials even when accidentally placed in ANON_KEY.
    privileged = key.startswith('sb_secret_')
    if key.count('.') == 2:
        try:
            payload = key.split('.')[1]
            role = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('role')
            privileged = privileged or role != 'anon'
        except (ValueError, TypeError, AttributeError):
            privileged = True
    if not valid or not key or privileged:
        raise StorageError('Der private Ergebnisspeicher ist nicht konfiguriert.', 503)
    return url, key

def config_status():
    """Public status: intentionally excludes hostnames, keys and schema details."""
    try:
        _configuration()
        return {'enabled': True}
    except (StorageError, ValueError):
        return {'enabled': False}

def _client():
    return httpx.Client(timeout=httpx.Timeout(connect=5, read=30, write=60, pool=5),
                        follow_redirects=False, trust_env=False)

def _token(token):
    if not isinstance(token, str) or not token or len(token) > 16384 or any(c.isspace() for c in token):
        raise StorageError('Bitte erneut anmelden.', 401)
    return token

def _request(method, path, *, token=None, params=None, payload=None,
             data=None, content_type=None, limit=MAX_JSON_BYTES, prefer=None):
    url, key = _configuration()
    headers = {'apikey': key}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + _token(token)
    if payload is not None:
        data = _json_bytes(payload)
        content_type = 'application/json'
    if content_type:
        headers['Content-Type'] = content_type
    if prefer:
        headers['Prefer'] = prefer
    try:
        with _client() as client:
            with client.stream(method, url + path, params=params, headers=headers, content=data) as response:
                if not 200 <= response.status_code < 300:
                    status = response.status_code
                    if status in (401, 403):
                        raise StorageError('Anmeldung oder Berechtigung fehlt.', status)
                    if status == 404:
                        raise StorageError('Gespeichertes Ergebnis nicht gefunden.', 404)
                    if status == 429:
                        raise StorageError('Zu viele Anfragen. Bitte später erneut versuchen.', 429)
                    if status in (400, 422) and path.startswith('/auth/'):
                        raise StorageError('Anmeldung oder Registrierung nicht möglich. Eingaben und E-Mail-Bestätigung prüfen.', 400)
                    raise StorageError('Der private Ergebnisspeicher konnte die Anfrage nicht abschließen.')
                result = bytearray()
                for chunk in response.iter_bytes():
                    if len(result) + len(chunk) > limit:
                        raise StorageError('Die gespeicherte Antwort überschreitet die zulässige Größe.')
                    result.extend(chunk)
                return bytes(result)
    except httpx.HTTPError:
        raise StorageError('Der private Ergebnisspeicher ist derzeit nicht erreichbar.', 503) from None

def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        raise StorageError('Ungültige Ergebnisdaten.', 422) from None

def _parse(raw):
    def reject_constant(_value):
        raise ValueError('Non-finite JSON number')
    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise StorageError('Der private Ergebnisspeicher lieferte eine ungültige Antwort.') from None

def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise StorageError('Ungültige Ergebniskennung.', 422) from None

def _credentials(email, password):
    if (not isinstance(email, str) or not isinstance(password, str) or
            not 3 <= len(email) <= 320 or '@' not in email or not 1 <= len(password) <= 1024):
        raise StorageError('E-Mail-Adresse und Passwort prüfen.', 422)
    return {'email': email.strip(), 'password': password}

def auth_login(email, password):
    result = _parse(_request('POST', '/auth/v1/token', params={'grant_type': 'password'},
                            payload=_credentials(email, password)))
    if not isinstance(result, dict) or not result.get('access_token'):
        raise StorageError('Anmeldung konnte nicht abgeschlossen werden.', 401)
    result['user'] = auth_get_user(result['access_token'])
    return result

def auth_signup(email, password):
    result = _parse(_request('POST', '/auth/v1/signup', payload=_credentials(email, password)))
    if not isinstance(result, dict):
        raise StorageError('Registrierung konnte nicht abgeschlossen werden.')
    return result

def auth_get_user(user_token):
    user = _parse(_request('GET', '/auth/v1/user', token=_token(user_token)))
    if not isinstance(user, dict) or not user.get('id'):
        raise StorageError('Bitte erneut anmelden.', 401)
    try:
        user['id'] = _uuid(user['id'])
    except StorageError:
        raise StorageError('Bitte erneut anmelden.', 401) from None
    try:
        allowed = _parse(_request('POST', '/rest/v1/rpc/dose_atlas_has_access',
                                 token=user_token, payload={}))
    except StorageError:
        raise StorageError('Zugang nicht freigeschaltet.', 403) from None
    if allowed is not True:
        raise StorageError('Zugang nicht freigeschaltet.', 403)
    return user

def _filename(name):
    if not isinstance(name, str) or name not in ALLOWED_FILES:
        raise StorageError('Diese Ergebnisdatei ist nicht verfügbar.', 404)
    return name

def _row(row, owner, run_id=None):
    if (not isinstance(row, dict) or row.get('owner_id') != owner or
            (run_id is not None and row.get('id') != run_id)):
        raise StorageError('Gespeichertes Ergebnis nicht gefunden.', 404)
    _uuid(row.get('id'))
    row = dict(row)
    files = row.get('summary', {}).get('_files', [])
    row['summary'] = dict(compact_summary(row.get('summary')), _files=[n for n in files if n in ALLOWED_FILES])
    return row

def _files(row):
    summary = row.get('summary')
    files = summary.get('_files') if isinstance(summary, dict) else None
    if (not isinstance(files, list) or any(not isinstance(name, str) for name in files) or
            'result.json' not in files or len(files) != len(set(files))):
        raise StorageError('Das gespeicherte Ergebnis ist unvollständig.')
    return [name for name in files if name in ALLOWED_FILES]

def _read_run(token, owner, run_id):
    rows = _parse(_request('GET', TABLE, token=token,
                          params={'id': 'eq.' + run_id, 'owner_id': 'eq.' + owner,
                                  'status': 'eq.complete', 'select': '*', 'limit': '1'}))
    if not isinstance(rows, list) or not rows:
        raise StorageError('Gespeichertes Ergebnis nicht gefunden.', 404)
    row = _row(rows[0], owner, run_id)
    if row.get('status') != 'complete':
        raise StorageError('Gespeichertes Ergebnis nicht gefunden.', 404)
    return row

def _download(token, owner, run_id, name):
    return _request('GET', f'/storage/v1/object/authenticated/{BUCKET}/{owner}/{run_id}/{name}',
                    token=token, limit=MAX_RESULT_BYTES if name.endswith('.json') else MAX_FILE_BYTES)

METRIC_KEYS = ('D98_Gy','D95_Gy','Dmean_Gy','D2_Gy','V100_pct',
               'inverse_CI','local_Paddick_CI','local_inverse_CI','local_GI','local_V12_cc','mean_local_inverse_CI','mean_local_CI','mean_local_GI','mean_local_inverse_CI_n','mean_local_CI_n','mean_local_GI_n',
               'target_volume_cc','Paddick_CI','GI','V12_domain_cc','V12_outside_targets_domain_cc')
SUMMARY_KEYS = ('prediction_calibration_scale','prediction_calibration_version','local_metrics_version','prediction_regularization_sigma_mm','prescription_Gy','fractions','target_count','grid_mm','reference_coverage_pct',
                'synthetic_demo','max_display_Gy')
RING_KEYS = ('ring_volume_cc','coverage_pct','predicted_auc_cc','reference_auc_cc','ratio',
             'predicted_D98_Gy','reference_D98_Gy','reference_V100_pct')

def _numbers(value, keys):
    value = value if isinstance(value, dict) else {}
    return {k: value[k] for k in keys if k in value and
            (value[k] is None or type(value[k]) in (int,float,bool) and
             math.isfinite(value[k]) and abs(value[k]) < 1e12)}

def _prescription(value):
    return type(value) in (int,float) and math.isfinite(value) and 0 < value <= 100

def _roi_number(value):
    # Canonical numerical ROI identifiers only; exclude booleans and arbitrary
    # strings/UIDs. These are analysis keys, never patient or DICOM identifiers.
    if type(value) is int:
        return value if 0 < value <= 2147483647 else None
    if isinstance(value,str) and value.isascii() and value.isdecimal() and len(value)<=10:
        number=int(value)
        return number if str(number)==value and 0 < number <= 2147483647 else None
    return None

def _target_prescriptions(value):
    if not isinstance(value,dict) or not 1 <= len(value) <= 40:
        return None
    out={}
    for number,dose in value.items():
        number=_roi_number(number)
        if number is None or not _prescription(dose) or str(number) in out:
            return None
        out[str(number)]=dose
    return out

def _row_prescription(row,include_number=True):
    dose=row.get('prescription_Gy')
    out={'prescription_Gy':dose} if _prescription(dose) else {}
    for key in (('number','roi_number') if include_number else ('roi_number',)):
        number=_roi_number(row.get(key))
        if number is not None:out[key]=number
    return out

def compact_summary(value):
    value = value if isinstance(value, dict) else {}
    out = _numbers(value, SUMMARY_KEYS)
    prescriptions=_target_prescriptions(value.get('target_prescriptions'))
    if prescriptions is not None:
        out['target_prescriptions']=prescriptions
        doses=list(prescriptions.values())
        out['prescription_range_Gy']=[min(doses),max(doses)]
        out['mixed_prescriptions']=min(doses)!=max(doses)
    else:
        interval=value.get('prescription_range_Gy')
        if (isinstance(interval,list) and len(interval)==2 and
                all(_prescription(x) for x in interval) and interval[0]<=interval[1]):
            out['prescription_range_Gy']=list(interval)
        if type(value.get('mixed_prescriptions')) is bool:
            out['mixed_prescriptions']=value['mixed_prescriptions']
    out['domain'] = '35-mm target neighbourhood'
    for key in ('predicted','reference'):
        out[key] = _numbers(value[key], METRIC_KEYS) if isinstance(value.get(key),dict) else None
    return out

def compact_result(value):
    """Strict, bounded archive schema. Never retain pixels, contours or identifiers."""
    if not isinstance(value, dict):
        raise StorageError('Ungültige Ergebnisdaten.', 422)
    out = dict(metadata_only=True, archive_schema=1, summary=compact_summary(value.get('summary')),
               warnings=['Archiv enthält nur Kennzahlen, DVHs und vereinfachte Zielgeometrie. DICOM und Dosisschichten werden nicht gespeichert.'],
               metrics=[], dvhs=[], targets3d=[], downloads=[],
               model={'name':'Dose Atlas','status':'Research'})
    if out['summary'].get('prediction_regularization_sigma_mm') in (.5,1.):
        out['warnings'].append('Vorhersage räumlich regularisiert; gespeicherte Kennzahlen und DVHs stammen aus dieser Dosis. Referenzdosis unverändert.')
    if out['summary'].get('prediction_calibration_scale',1)>1:
        out['warnings'].append('Empirische Dosiskalibrierung; Faktor und Version sind in den Metadaten gespeichert. Keine erreichbare Optimaldosis.')
    labels = {}
    def label(name, role='target'):
        name = name if isinstance(name,str) else ''
        if name not in labels: labels[name] = ('Organ' if role == 'organ' else 'Target') + ' ' + str(len(labels)+1)
        return labels[name]
    for row in value.get('metrics',[])[:64] if isinstance(value.get('metrics'),list) else []:
        if not isinstance(row,dict): continue
        role = 'organ' if row.get('role') == 'organ' else 'target'
        item = dict(roi=label(row.get('roi'),role),role=role,**_numbers(row,('volume_cc','local_coverage_pct','local_region_cc')),**_row_prescription(row))
        for key in ('predicted','reference'):
            item[key] = _numbers(row[key],METRIC_KEYS) if isinstance(row.get(key),dict) else None
        out['metrics'].append(item)
    def curve(points):
        if not isinstance(points,list): return None
        # Keep duplicate-dose staircase pairs intact. Subsampling individual
        # points would change empirical DVH semantics; the producer bounds them.
        if len(points)>404:
            raise StorageError('Eine DVH überschreitet die zulässigen 404 Stufenpunkte.',422)
        return [[float(p[0]),float(p[1])] for p in points if isinstance(p,list) and len(p)==2
                and all(type(x) in (int,float) and math.isfinite(x) and abs(x)<1e12 for x in p)]
    for row in value.get('dvhs',[])[:64] if isinstance(value.get('dvhs'),list) else []:
        if isinstance(row,dict):
            out['dvhs'].append(dict(roi=label(row.get('roi')),predicted=curve(row.get('predicted')),reference=curve(row.get('reference')),**_row_prescription(row)))
    for row in value.get('targets3d',[])[:40] if isinstance(value.get('targets3d'),list) else []:
        if not isinstance(row,dict): continue
        item = dict(name=label(row.get('name')),number=_roi_number(row.get('number')) or len(out['targets3d'])+1,**_numbers(row,('radius_mm',)),**_row_prescription(row,include_number=False))
        for key in ('center_lps_mm','bounds_min_lps_mm','bounds_max_lps_mm'):
            v=row.get(key)
            if isinstance(v,list) and len(v)==3 and all(type(x) in (int,float) and math.isfinite(x) and abs(x)<1e6 for x in v):
                item[key]=[round(x,2) for x in v]
        out['targets3d'].append(item)
    ring=value.get('ring_benchmark',{})
    ring=ring if isinstance(ring,dict) else {}
    out['ring_benchmark']=dict(ring_width_mm=10,relative_dose_interval=[.5,.8],
        definition='10-mm-Außenring je Target; alle Targets ausgeschlossen; Ringe können überlappen.',rows=[])
    for row in ring.get('rows',[])[:40] if isinstance(ring.get('rows'),list) else []:
        if isinstance(row,dict):
            item=dict(roi=label(row.get('roi')),**_numbers(row,RING_KEYS),**_row_prescription(row))
            item['reason']='Nicht vollständig auswertbar.' if row.get('reason') else None
            out['ring_benchmark']['rows'].append(item)
    # Regenerate interpretation from bounded numeric metadata, never carry
    # arbitrary input warnings (which can contain identifying clinical text).
    doses=list(out['summary'].get('target_prescriptions',{}).values())
    doses+=out['summary'].get('prescription_range_Gy',[])
    if _prescription(out['summary'].get('prescription_Gy')):
        doses.append(out['summary']['prescription_Gy'])
    for rows in (out['metrics'],out['dvhs'],out['targets3d'],out['ring_benchmark']['rows']):
        doses.extend(row['prescription_Gy'] for row in rows if 'prescription_Gy' in row)
    if out['summary'].get('mixed_prescriptions') is True or len(set(doses))>1:
        out['warnings'].append('Unterschiedliche Zielverschreibungen: Kennzahlen je Target auf dessen Verschreibung beziehen; globale CI/GI sind nicht definiert.')
    if any(dose<18 or dose>20 for dose in doses):
        out['warnings'].append('Mindestens eine Zielverschreibung liegt außerhalb von 18–20 Gy: Modellextrapolation, nicht validiert.')
    if len(_json_bytes(out)) > MAX_RESULT_BYTES:
        raise StorageError('Die Ergebniskennzahlen sind zu groß.',422)
    return out

def compact_model_card(value):
    # Model artifacts/training identifiers and arbitrary nested fields are excluded.
    return {'archive_schema':1,'name':'Dose Atlas','status':'Research',
            **_numbers(value,('version','grid_mm',))}

def store_analysis(user_token, bundle, summary):
    """Persist only schema-whitelisted compact JSON; DICOM is never accepted."""
    if not isinstance(bundle, dict) or 'result.json' not in bundle or not isinstance(summary,dict):
        raise StorageError('Vollständiges Ergebnis und Zusammenfassung erforderlich.',422)
    clean = {}
    for name,data in bundle.items():
        _filename(name)
        if not isinstance(data,bytes) or not data or len(data)>MAX_RESULT_BYTES:
            raise StorageError('Eine Ergebnisdatei ist leer oder zu groß.',422)
        value=_parse(data)
        if not isinstance(value,dict): raise StorageError('Ungültige Ergebnisdaten.',422)
        clean[name]=_json_bytes(compact_result(value) if name=='result.json' else compact_model_card(value))
    bundle=clean
    metadata=dict(compact_summary(summary),_files=sorted(bundle))
    owner = auth_get_user(user_token)['id']
    run_id = str(uuid4())
    row = {'id': run_id, 'owner_id': owner, 'summary': metadata, 'status': 'staging'}
    _request('POST', TABLE, token=user_token, payload=row, prefer='return=minimal')
    prefix = f'{owner}/{run_id}'
    uploaded = []
    completion_attempted = False
    try:
        for name, data in bundle.items():
            # Track before request: an uncertain response may still have saved it.
            uploaded.append(f'{prefix}/{name}')
            _request('POST', f'/storage/v1/object/{BUCKET}/{prefix}/{name}', token=user_token,
                     data=data, content_type=ALLOWED_FILES[name])
        completion_attempted = True
        updated = _parse(_request('PATCH', TABLE, token=user_token,
                                 params={'id': 'eq.' + run_id, 'owner_id': 'eq.' + owner,
                                         'status': 'eq.staging'},
                                 payload={'status': 'complete'}, prefer='return=representation'))
        if not isinstance(updated, list) or len(updated) != 1:
            raise StorageError('Das Ergebnis konnte nicht vollständig gespeichert werden.')
        final = _row(updated[0], owner, run_id)
        if final.get('status') != 'complete':
            raise StorageError('Das Ergebnis konnte nicht vollständig gespeichert werden.')
        return final
    except StorageError as failure:
        if completion_attempted:
            # All objects are already present. An uncertain PATCH may have committed;
            # never delete objects from a possibly visible, completed run.
            try:
                return _read_run(user_token, owner, run_id)
            except StorageError:
                raise failure from None
        # Best effort only: retain staging row if cleanup fails, enabling later cleanup.
        try:
            if uploaded:
                _request('DELETE', f'/storage/v1/object/{BUCKET}', token=user_token,
                         payload={'prefixes': uploaded})
            _request('DELETE', TABLE, token=user_token,
                     params={'id': 'eq.' + run_id, 'owner_id': 'eq.' + owner})
        except StorageError:
            pass
        raise

def list_runs(user_token, limit=50):
    owner = auth_get_user(user_token)['id']
    if type(limit) is not int or not 1 <= limit <= 100:
        raise StorageError('Ungültige Anzahl gespeicherter Ergebnisse.', 422)
    rows = _parse(_request('GET', TABLE, token=user_token,
                          params={'owner_id': 'eq.' + owner, 'status': 'eq.complete',
                                  'order': 'created_at.desc,id.desc', 'limit': str(limit), 'select': '*'},
                          limit=8 * 1024 * 1024))
    if not isinstance(rows, list):
        raise StorageError('Gespeicherte Ergebnisse konnten nicht gelesen werden.')
    return [_row(row, owner) for row in rows if isinstance(row, dict) and row.get('status') == 'complete']

def load_run(user_token, run_id):
    run_id = _uuid(run_id)
    owner = auth_get_user(user_token)['id']
    row = _read_run(user_token, owner, run_id)
    names = _files(row)
    result = _parse(_download(user_token, owner, run_id, 'result.json'))
    if not isinstance(result, dict):
        raise StorageError('Das gespeicherte Ergebnis enthält ungültige Daten.')
    return {'run': row, 'result': compact_result(result), 'files': names}

def download_file(user_token, run_id, name):
    run_id, name = _uuid(run_id), _filename(name)
    owner = auth_get_user(user_token)['id']
    row = _read_run(user_token, owner, run_id)
    if name not in _files(row):
        raise StorageError('Diese Ergebnisdatei ist nicht verfügbar.', 404)
    value = _parse(_download(user_token, owner, run_id, name))
    clean = compact_result(value) if name == 'result.json' else compact_model_card(value)
    return _json_bytes(clean), ALLOWED_FILES[name]
