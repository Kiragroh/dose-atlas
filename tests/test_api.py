from io import BytesIO
import json
import numpy as np
from pydicom.dataset import FileDataset, FileMetaDataset, Dataset
from pydicom.uid import ExplicitVRLittleEndian, RTStructureSetStorage
from fastapi.testclient import TestClient
from app import app
from geometry import structure_rois, InputError
import pytest


def structure_bytes():
    meta=FileMetaDataset();meta.TransferSyntaxUID=ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID=RTStructureSetStorage;meta.MediaStorageSOPInstanceUID='1.2.3.4'
    ds=FileDataset(None,{},file_meta=meta,preamble=b'\0'*128)
    ds.Modality='RTSTRUCT';ds.SOPClassUID=RTStructureSetStorage;ds.SOPInstanceUID='1.2.3.4'
    ds.PatientName='SYNTHETIC';ds.PatientID='SYNTHETIC'
    roi=Dataset();roi.ROINumber=1;roi.ROIName='PTV demo';roi.ReferencedFrameOfReferenceUID='1.2.3'
    ds.StructureSetROISequence=[roi]
    rc=Dataset();rc.ReferencedROINumber=1;rc.ContourSequence=[]
    for z in [-2.,-1.,0.,1.,2.]:
        c=Dataset();c.ContourGeometricType='CLOSED_PLANAR';c.NumberOfContourPoints=4
        c.ContourData=[-3.,-3.,z,3.,-3.,z,3.,3.,z,-3.,3.,z];rc.ContourSequence.append(c)
    ds.ROIContourSequence=[rc]
    ds.is_little_endian=True;ds.is_implicit_VR=False
    stream=BytesIO();ds.save_as(stream,write_like_original=False)
    return stream.getvalue(),ds


def test_inspect_and_no_identifiers_returned():
    data,_=structure_bytes()
    with TestClient(app) as client:
        response=client.post('/api/inspect',files={'structure':('test.dcm',data,'application/dicom')})
        assert response.status_code==200
        assert response.json()['suggested_targets']==[1]
        assert 'SYNTHETIC' not in response.text and '1.2.3' not in response.text
        assert response.headers['Cache-Control']=='no-store'


def test_invalid_file_rx_fractions_targets_and_size():
    data,_=structure_bytes()
    with TestClient(app) as client:
        r=client.post('/api/inspect',files={'structure':('bad.dcm',b'PatientName=private','application/dicom')})
        assert r.status_code==422 and 'private' not in r.text
        for settings in [dict(prescription='101',fractions='1',targets='[1]'),
                         dict(prescription='20',fractions='5',targets='[1]'),
                         dict(prescription='20',fractions='1',targets='[1,1]')]:
            r=client.post('/api/predict',files={'structure':('test.dcm',data)},data=settings)
            assert r.status_code==422
        assert client.post('/api/inspect',headers={'content-length':str(251*1024*1024)}).status_code==413


def test_mixed_contours_rejected():
    _,ds=structure_bytes();c=Dataset();c.ContourGeometricType='OPEN_PLANAR'
    ds.ROIContourSequence[0].ContourSequence.append(c)
    with pytest.raises(InputError):structure_rois(ds)


def test_model_inference_and_missing_reference():
    from pathlib import Path
    if not (Path(__file__).parents[1]/'artifacts'/'model.joblib').exists():pytest.skip('Model not trained')
    data,_=structure_bytes()
    with TestClient(app) as client:
        r=client.post('/api/predict',files={'structure':('test.dcm',data)},
                      data={'prescription':'20','fractions':'1','targets':'[1]'})
    assert r.status_code==200,r.text
    result=r.json()
    assert result['metrics'][0]['predicted']['D98_Gy']>0
    assert result['metrics'][0]['reference'] is None
    assert result['slices']['reference'] is None
    assert any('Extrapolation' in w for w in result['warnings'])


def test_organ_in_other_frame_never_gets_dvh():
    from app import response_data
    from geometry import Grid
    contours=[np.array([[-1,-1,z],[1,-1,z],[1,1,z],[-1,1,z]],float) for z in (0,1)]
    rois={1:dict(name='PTV',contours=contours,frame='1.2.3'),
          2:dict(name='Brainstem',contours=contours,frame='1.2.4')}
    grid=Grid(np.array([-3.,-3.,-3.]),(8,8,8));labels=np.zeros(grid.shape,np.int16);labels[3,3,3]=1
    X=np.zeros((labels.size,8),np.float32);pred=np.ones(grid.shape)*20
    result=response_data(rois,[1],20,grid,X,labels,[dict(volume_cc=.001)],pred)
    assert [m['roi'] for m in result['metrics']]==['PTV']
    assert any('anderen Referenzrahmen' in w for w in result['warnings'])


def test_upload_admission_is_bounded_and_does_not_read_rejected_body():
    import asyncio
    from app import SizeLimit
    called=[]
    async def underlying(scope,receive,send):called.append(True)
    middleware=SizeLimit(underlying)
    async def run():
        messages=[]
        async def receive():raise AssertionError('Rejected concurrent upload body was read')
        async def send(message):messages.append(message)
        middleware.upload_lock.acquire()
        try:await middleware({'type':'http','method':'POST','headers':[]},receive,send)
        finally:middleware.upload_lock.release()
        assert messages[0]['status']==429
        assert not called
    asyncio.run(run())
