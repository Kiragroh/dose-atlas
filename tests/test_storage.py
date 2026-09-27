"""Offline transport tests. No real Supabase endpoint or patient files are used."""
import base64
import json
from pathlib import Path
import httpx
import pytest
import storage
OWNER = '10000000-0000-0000-0000-000000000001'
OTHER = '20000000-0000-0000-0000-000000000002'
RUN = '30000000-0000-0000-0000-000000000003'
TOKEN = 'synthetic-user-token'
RESULT = {'summary': {'synthetic_demo': True}, 'slices': {'predicted': [[[20.0]]]}}

@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'http://supabase.invalid')
    monkeypatch.setenv('SUPABASE_ANON_KEY', 'synthetic-anon-key')
    monkeypatch.delenv('SUPABASE_PUBLISHABLE_KEY', raising=False)
    # Fail closed if any test forgets to replace the transport.
    def blocked():
        raise AssertionError('Real HTTP access forbidden in storage tests')
    monkeypatch.setattr(storage, '_client', blocked)
    monkeypatch.setattr(storage, 'uuid4', lambda: RUN)

def transport(monkeypatch, handler):
    original_handler = handler
    def handler(request):
        if request.url.path == '/rest/v1/rpc/dose_atlas_has_access':
            assert request.headers['authorization'] == 'Bearer ' + TOKEN
            return httpx.Response(200,json=True)
        return original_handler(request)
    monkeypatch.setattr(storage, '_client', lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))

def row(owner=OWNER):
    return {'id': RUN, 'owner_id': owner, 'summary': {'_files': ['model_card.json', 'result.json']},
            'status': 'complete', 'created_at': '2026-09-21T00:00:00Z'}

def test_status_and_privileged_key_rejection(monkeypatch):
    assert storage.config_status() == {'enabled': True}
    for key in ['sb_secret_never_use', 'a.' + base64.urlsafe_b64encode(
            b'{"role":"service_role"}').decode().rstrip('=') + '.b']:
        monkeypatch.setenv('SUPABASE_ANON_KEY', key)
        assert storage.config_status() == {'enabled': False}
    monkeypatch.delenv('SUPABASE_ANON_KEY')
    assert storage.config_status() == {'enabled': False}

def test_auth_contract_and_no_untrusted_error_details(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['apikey'] == 'synthetic-anon-key'
        if request.url.path == '/auth/v1/user':
            assert request.headers['authorization'] == 'Bearer ' + TOKEN
            return httpx.Response(200, json={'id': OWNER, 'email': 'synthetic@example.invalid'})
        assert json.loads(request.content) == {'email': 'synthetic@example.invalid', 'password': 'not-a-secret'}
        if request.url.path == '/auth/v1/signup':
            return httpx.Response(200, json={'id': OWNER})
        assert request.url.params['grant_type'] == 'password'
        return httpx.Response(200, json={'access_token': TOKEN, 'user': {'id': OWNER}})
    transport(monkeypatch, handler)
    assert storage.auth_login('synthetic@example.invalid', 'not-a-secret')['access_token'] == TOKEN
    assert storage.auth_signup('synthetic@example.invalid', 'not-a-secret')['id'] == OWNER
    assert storage.auth_get_user(TOKEN)['id'] == OWNER
    assert len(calls) == 4
    transport(monkeypatch, lambda r: httpx.Response(400, json={'error': 'private-identifier-and-secret'}))
    with pytest.raises(storage.StorageError) as exc:
        storage.auth_login('synthetic@example.invalid', 'not-a-secret')
    assert exc.value.status_code == 400
    assert 'private-identifier' not in str(exc.value)

def test_store_forwards_rls_token_and_completes_only_after_uploads(monkeypatch):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        if request.url.path == '/auth/v1/user':
            return httpx.Response(200, json={'id': OWNER})
        if request.method == 'POST' and request.url.path == storage.TABLE:
            body = json.loads(request.content)
            assert body['owner_id'] == OWNER and body['status'] == 'staging'
            assert body['summary']['_files'] == ['model_card.json', 'result.json']
            return httpx.Response(201)
        if request.method == 'PATCH':
            assert len(calls) == 5
            assert request.url.params['owner_id'] == 'eq.' + OWNER
            assert json.loads(request.content) == {'status': 'complete'}
            return httpx.Response(200, json=[row()])
        assert request.url.path.startswith(f'/storage/v1/object/dose-atlas/{OWNER}/{RUN}/')
        assert 'x-upsert' not in request.headers
        return httpx.Response(200, json={'Key': 'synthetic'})
    transport(monkeypatch, handler)
    saved = storage.store_analysis(TOKEN, {'result.json': json.dumps(RESULT).encode(),
                                          'model_card.json': b'{}'}, {'synthetic_demo': True})
    assert saved['status'] == 'complete'

def test_failed_upload_cleans_objects_before_row_without_completing(monkeypatch):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/auth/v1/user':
            return httpx.Response(200, json={'id': OWNER})
        if request.method == 'POST' and request.url.path.startswith('/storage/'):
            return httpx.Response(500, text='internal private data')
        if request.method == 'DELETE' and request.url.path.startswith('/storage/'):
            assert json.loads(request.content)['prefixes'] == [f'{OWNER}/{RUN}/result.json']
        return httpx.Response(201 if request.method == 'POST' else 200)
    transport(monkeypatch, handler)
    with pytest.raises(storage.StorageError) as exc:
        storage.store_analysis(TOKEN, {'result.json': b'{}'}, {})
    assert 'private data' not in str(exc.value)
    assert calls[-2:] == [('DELETE', '/storage/v1/object/dose-atlas'), ('DELETE', storage.TABLE)]
    assert not any(method == 'PATCH' for method, _ in calls)

def test_uncertain_completion_readback_preserves_completed_files(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.url.path == '/auth/v1/user':
            return httpx.Response(200, json={'id': OWNER})
        if request.method == 'PATCH':
            raise httpx.ReadTimeout('never surfaced', request=request)
        if request.method == 'GET':
            return httpx.Response(200, json=[row()])
        return httpx.Response(201, json={})
    transport(monkeypatch, handler)
    assert storage.store_analysis(TOKEN, {'result.json': b'{}'}, {})['status'] == 'complete'
    assert 'DELETE' not in calls

def test_list_load_download_are_owner_filtered_and_bounded(monkeypatch):
    def handler(request):
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        if request.url.path == '/auth/v1/user':
            return httpx.Response(200, json={'id': OWNER})
        if request.url.path == storage.TABLE:
            assert request.url.params['owner_id'] == 'eq.' + OWNER
            assert request.url.params['status'] == 'eq.complete'
            return httpx.Response(200, json=[row()])
        assert request.url.path.startswith(f'/storage/v1/object/authenticated/dose-atlas/{OWNER}/{RUN}/')
        return httpx.Response(200, content=json.dumps(RESULT).encode() if request.url.path.endswith('.json') else b'dicom')
    transport(monkeypatch, handler)
    assert storage.list_runs(TOKEN)[0]['id'] == RUN
    assert storage.load_run(TOKEN, RUN)['result'] == storage.compact_result(RESULT)
    assert json.loads(storage.download_file(TOKEN, RUN, 'result.json')[0]) == storage.compact_result(RESULT)

@pytest.mark.parametrize('name', ['../result.json', '/result.json', f'{OTHER}/{RUN}/result.json', 'patient-name.dcm'])
def test_path_traversal_and_original_filenames_fail_before_network(name):
    with pytest.raises(storage.StorageError):
        storage.download_file(TOKEN, RUN, name)

def test_cross_owner_row_never_reaches_storage(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'id': OWNER} if request.url.path == '/auth/v1/user' else [row(OTHER)])
    transport(monkeypatch, handler)
    with pytest.raises(storage.StorageError) as exc:
        storage.load_run(TOKEN, RUN)
    assert exc.value.status_code == 404
    assert not any(path.startswith('/storage/') for path in calls)

def test_invalid_bundle_and_json_fail_without_network():
    for bundle, summary in [({}, {}), ({'result.json': b'{}', 'original-name.dcm': b'x'}, {}),
                            ({'result.json': b'[]'}, {}), ({'result.json': b'{"x": NaN}'}, {}),
                            ({'result.json': b'{}', 'prediction.dcm':b'forbidden'}, {})]:
        with pytest.raises(storage.StorageError):
            storage.store_analysis(TOKEN, bundle, summary)

def test_oversize_response_and_transport_failures_are_sanitized(monkeypatch):
    transport(monkeypatch, lambda r: httpx.Response(200, content=b'123456'))
    with pytest.raises(storage.StorageError):
        storage._request('GET', '/auth/v1/user', token=TOKEN, limit=5)
    def timeout(request):
        raise httpx.ConnectTimeout('host-with-private-details', request=request)
    transport(monkeypatch, timeout)
    with pytest.raises(storage.StorageError) as exc:
        storage.auth_get_user(TOKEN)
    assert exc.value.status_code == 503 and 'private-details' not in str(exc.value)

def test_sql_uses_owner_rls_and_bucket_restrictive_guards():
    sql = (Path(__file__).parents[1] / 'deploy' / 'supabase.sql').read_text()
    assert 'grant update(status)' in sql
    assert 'force row level security' in sql
    assert sql.count('as restrictive') == 7
    assert "status = 'staging'" in sql and '(select auth.uid())' in sql
    assert "'dose-atlas', 'dose-atlas', false" in sql

def test_archive_whitelist_excludes_identifiers_pixels_contours_and_bounds_payload():
    raw={'summary':dict(prescription_Gy=20,PatientID='PRIVATE',uid='1.2.3'),
         'slices':{'predicted':[[[123]]]},'PixelData':'SECRET','SOPInstanceUID':'1.2.3',
         'warnings':['PRIVATE'], 'metrics':[dict(roi='PRIVATE',role='target',predicted={'D98_Gy':19,'uid':'1.2.3'})],
         'targets3d':[dict(name='PRIVATE',center_lps_mm=[1,2,3],radius_mm=4,contours=[[[1,2,3]]])],
         'dvhs':[dict(roi='PRIVATE',predicted=[[i,i] for i in range(201)])]*200}
    clean=storage.compact_result(raw)
    encoded=json.dumps(clean)
    assert all(x not in encoded for x in ('PRIVATE','PixelData','SOPInstanceUID','contours','slices','1.2.3'))
    assert len(clean['dvhs'])==64 and len(clean['dvhs'][0]['predicted'])==201
    assert clean['targets3d'][0]['center_lps_mm']==[1,2,3]
    assert len(storage._json_bytes(clean))<=storage.MAX_RESULT_BYTES

def test_mixed_prescriptions_and_step_curves_survive_archive_without_identifiers():
    steps=[[0,100],[8,100],[8,50],[12,50],[12,0]]
    summary={'prescription_Gy':None,'target_prescriptions':{'51':18,'83':24},
             'prescription_range_Gy':[18,24],'mixed_prescriptions':True,
             'predicted':{'Paddick_CI':None,'GI':None,'V12_domain_cc':2.5},
             'reference':{'Paddick_CI':None,'GI':None},'PatientID':'PRIVATE'}
    rows=[{'roi':'PRIVATE','name':'PRIVATE','number':51,'roi_number':51,
           'prescription_Gy':18,'predicted':steps,'reference':steps,'PixelData':'PRIVATE',
           'SOPInstanceUID':'1.2.3.PRIVATE','contours':[[[1,2,3]]]}]
    raw={'summary':summary,'metrics':[dict(rows[0],predicted={'GI':1.4},reference=None)],
         'targets3d':rows,'dvhs':rows,'ring_benchmark':{'rows':rows}}
    clean=storage.compact_result(raw)
    assert clean['summary']['target_prescriptions']=={'51':18,'83':24}
    assert clean['summary']['mixed_prescriptions'] is True
    assert clean['summary']['prescription_range_Gy']==[18,24]
    assert clean['summary']['predicted']['Paddick_CI'] is None
    assert clean['summary']['reference']['GI'] is None
    assert clean['targets3d'][0]['number']==51
    for entries in (clean['targets3d'],clean['metrics'],clean['dvhs'],clean['ring_benchmark']['rows']):
        assert entries[0]['prescription_Gy']==18 and entries[0]['roi_number']==51
    assert clean['dvhs'][0]['predicted']==steps
    assert storage.compact_result(clean)==clean
    assert any('Unterschiedliche Zielverschreibungen' in s for s in clean['warnings'])
    assert any('Modellextrapolation' in s for s in clean['warnings'])
    payload=storage._json_bytes(clean)
    assert all(x not in payload for x in (b'PRIVATE',b'PixelData',b'SOPInstanceUID',b'contours'))

@pytest.mark.parametrize('mapping',[{}, {'PRIVATE':20}, {'1.2.3':20}, {'01':20}, {'-1':20},
    {'1':True},{'1':'20'},{'1':{'PixelData':'PRIVATE'}},{'1':None},{'1':float('nan')},
    {'1':0},{'1':101},{'1':float('inf')},{str(n):20 for n in range(1,42)}])
def test_prescription_map_rejects_noncanonical_or_unbounded_payloads(mapping):
    assert 'target_prescriptions' not in storage.compact_summary({'target_prescriptions':mapping})

def test_prescription_metadata_is_bounded_and_derived_from_map():
    clean=storage.compact_summary({'target_prescriptions':{'5':20,'51':20},
                                  'mixed_prescriptions':True,'prescription_range_Gy':[1,99]})
    assert clean['mixed_prescriptions'] is False and clean['prescription_range_Gy']==[20,20]
    for invalid in (True,0,101,float('inf'),'PRIVATE',{'PixelData':'PRIVATE'}):
        clean=storage.compact_result({'dvhs':[{'prescription_Gy':invalid,'number':'PRIVATE'}]})
        assert 'prescription_Gy' not in clean['dvhs'][0] and 'number' not in clean['dvhs'][0]
    assert 'prescription_range_Gy' not in storage.compact_summary({'prescription_range_Gy':[30,20]})
    assert 'mixed_prescriptions' not in storage.compact_summary({'mixed_prescriptions':'PRIVATE'})

def test_archive_preserves_all_404_staircase_points_and_rejects_overflow():
    steps=[[float(i//2),100-i/4.04] for i in range(404)]
    clean=storage.compact_result({'dvhs':[{'predicted':steps}]})
    assert clean['dvhs'][0]['predicted']==steps
    with pytest.raises(storage.StorageError) as error:
        storage.compact_result({'dvhs':[{'predicted':steps+[[202,0]]}]})
    assert error.value.status_code==422

def test_archive_regenerates_only_numeric_prescription_warnings():
    same=storage.compact_result({'summary':{'target_prescriptions':{'1':20,'2':20}},'warnings':['PRIVATE']})
    assert len(same['warnings'])==1
    legacy=storage.compact_result({'summary':{'prescription_Gy':24},'warnings':['PRIVATE']})
    assert len(legacy['warnings'])==2 and 'Modellextrapolation' in legacy['warnings'][1]
    rows=storage.compact_result({'metrics':[{'prescription_Gy':18},{'prescription_Gy':24}]})
    assert len(rows['warnings'])==3

def test_persisted_mixed_rx_json_keeps_metadata_and_excludes_dicom(monkeypatch):
    posted=[]
    def handler(request):
        if request.url.path=='/auth/v1/user':return httpx.Response(200,json={'id':OWNER})
        if request.method=='PATCH':return httpx.Response(200,json=[row()])
        if request.method=='POST':posted.append(json.loads(request.content))
        return httpx.Response(201)
    transport(monkeypatch,handler)
    summary={'target_prescriptions':{'51':18,'83':24},'mixed_prescriptions':True,
             'predicted':{'Paddick_CI':None,'GI':None},'PatientID':'PRIVATE'}
    result={'summary':summary,'PixelData':'PRIVATE','dicom':'PRIVATE',
            'dvhs':[{'roi':'PRIVATE','roi_number':51,'prescription_Gy':18,
                     'predicted':[[0,100],[18,100],[18,0]],'reference':None}]}
    storage.store_analysis(TOKEN,{'result.json':json.dumps(result).encode()},summary)
    staged,uploaded=posted
    assert staged['summary']['target_prescriptions']=={'51':18,'83':24}
    assert uploaded['summary']['prescription_range_Gy']==[18,24]
    assert uploaded['summary']['predicted']['GI'] is None
    assert uploaded['dvhs'][0]['prescription_Gy']==18
    assert uploaded['dvhs'][0]['predicted']==[[0,100],[18,100],[18,0]]
    assert b'PRIVATE' not in storage._json_bytes(posted) and b'PixelData' not in storage._json_bytes(posted)

def test_storage_sanitizes_before_upload_and_legacy_load(monkeypatch):
    posted=[]
    def handler(request):
        if request.url.path=='/auth/v1/user':return httpx.Response(200,json={'id':OWNER})
        if request.method=='PATCH':return httpx.Response(200,json=[row()])
        if request.method=='POST':
            posted.append(request.content)
            return httpx.Response(201)
        if request.url.path==storage.TABLE:
            legacy=row();legacy['summary']['_files']=['result.json','prediction.dcm']
            return httpx.Response(200,json=[legacy])
        return httpx.Response(200,json=RESULT)
    transport(monkeypatch,handler)
    storage.store_analysis(TOKEN,{'result.json':json.dumps(RESULT).encode()}, {'PatientID':'PRIVATE'})
    assert b'slices' not in b''.join(posted) and b'PRIVATE' not in b''.join(posted)
    loaded=storage.load_run(TOKEN,RUN)
    assert loaded['files']==['result.json'] and 'slices' not in loaded['result']
    with pytest.raises(storage.StorageError):storage.download_file(TOKEN,RUN,'prediction.dcm')


@pytest.mark.parametrize('allowed',[False,None,{},[True],1,'true'])
def test_access_rpc_requires_exact_boolean_true(monkeypatch,allowed):
    def handler(request):
        if request.url.path=='/auth/v1/user':return httpx.Response(200,json={'id':OWNER})
        return httpx.Response(200,json=allowed)
    monkeypatch.setattr(storage,'_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(storage.StorageError) as error:storage.auth_get_user(TOKEN)
    assert error.value.status_code==403 and str(error.value)=='Zugang nicht freigeschaltet.'


def test_allowlist_unavailable_fails_closed_and_blocks_login(monkeypatch):
    def handler(request):
        if request.url.path=='/auth/v1/token':return httpx.Response(200,json={'access_token':TOKEN})
        if request.url.path=='/auth/v1/user':return httpx.Response(200,json={'id':OWNER})
        return httpx.Response(404,json={'error':'private internal information'})
    monkeypatch.setattr(storage,'_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(storage.StorageError) as error:storage.auth_login('user@example.invalid','password')
    assert error.value.status_code==403 and str(error.value)=='Zugang nicht freigeschaltet.'


def test_sql_allowlist_is_private_verified_and_app_scoped():
    sql=(Path(__file__).parents[1]/'deploy'/'supabase.sql').read_text()
    assert 'revoke all on public.dose_atlas_allowed_emails from public, anon, authenticated' in sql
    assert "set search_path = ''" in sql and 'security definer' in sql
    assert 'u.id = auth.uid() and u.email_confirmed_at is not null and a.enabled' in sql
    assert "bucket_id <> 'dose-atlas' or (select public.dose_atlas_has_access())" in sql
