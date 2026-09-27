"""Offline node/browser-core checks. Real data is opt-in and stays in pipe/RAM."""
import base64
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import pydicom
from pydicom.dataset import FileDataset,FileMetaDataset,Dataset
from pydicom.uid import ExplicitVRLittleEndian,ImplicitVRLittleEndian,generate_uid,RTDoseStorage,RTStructureSetStorage
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from upload import KEEP

def binary(ds):
    f=io.BytesIO();ds.save_as(f,write_like_original=False);return f.getvalue()

def bridge(rs,rd,predicted=None):
    data={k:base64.b64encode(v).decode() for k,v in [('structure',rs),('dose',rd),('predicted',predicted)] if v is not None}
    p=subprocess.run(['node',str(Path(__file__).with_name('browser_privacy_bridge.cjs'))],input=gzip.compress(json.dumps(data).encode(),compresslevel=1),capture_output=True)
    assert p.returncode==0,'Browser privacy rejected input'
    r=json.loads(gzip.decompress(p.stdout))
    return {k:pydicom.dcmread(io.BytesIO(base64.b64decode(r[k]))) for k in ('structure','dose','restored')}

def rejected(rs,rd):
    p=subprocess.run(['node',str(Path(__file__).with_name('browser_privacy_bridge.cjs'))],input=gzip.compress(json.dumps({k:base64.b64encode(v).decode() for k,v in [('structure',rs),('dose',rd)]}).encode(),compresslevel=1),capture_output=True)
    assert p.returncode!=0 and not p.stdout and p.stderr==b'PRIVACY_PARSE_FAILED'

def assert_pair(source_rs,source_rd,r):
    mapping={}
    for original,key in [(source_rs,'structure'),(source_rd,'dose')]:
        clean=r[key]
        assert clean.PatientID=='ANON' and clean.PatientName=='ANON'
        assert clean.SOPInstanceUID!=original.SOPInstanceUID
        assert clean.StudyInstanceUID!=original.StudyInstanceUID
        def compare(a,b):
            for e in b:
                assert e.keyword in KEEP|{'PatientName','PatientID','PatientIdentityRemoved','DeidentificationMethod'}
                assert not e.tag.is_private
                if e.VR=='SQ':
                    for x,y in zip(a[e.tag].value,e.value):compare(x,y)
                elif e.VR=='UI':
                    if e.keyword in {'SOPClassUID','ReferencedSOPClassUID'}:assert e.value==a[e.tag].value
                    else:
                        original_uid=str(a[e.tag].value);new_uid=str(e.value)
                        assert original_uid!=new_uid
                        assert mapping.setdefault(original_uid,new_uid)==new_uid
                elif e.keyword in KEEP and e.VR!='UI' and e.keyword!='ROIName':assert e.value==a[e.tag].value
        compare(original,clean)
    assert r['dose'].FrameOfReferenceUID==r['structure'].ReferencedFrameOfReferenceSequence[0].FrameOfReferenceUID
    restored=r['restored'];assert restored.StudyInstanceUID==source_rs.StudyInstanceUID
    assert restored.FrameOfReferenceUID==source_rd.FrameOfReferenceUID
    assert restored.PatientID==source_rs.PatientID
    assert restored.SOPInstanceUID==r['dose'].SOPInstanceUID
    assert restored.SeriesInstanceUID==r['dose'].SeriesInstanceUID
    assert restored.PixelData==source_rd.PixelData
    assert restored.ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID==source_rs.SOPInstanceUID

def synthetic_pair(implicit=False):
    frame=generate_uid();study=generate_uid()
    def make(mod,cls):
        meta=FileMetaDataset();meta.TransferSyntaxUID=ImplicitVRLittleEndian if implicit else ExplicitVRLittleEndian
        ds=FileDataset(None,{},file_meta=meta,preamble=b'X'*128)
        ds.is_little_endian=True;ds.is_implicit_VR=implicit
        ds.Modality=mod;ds.SOPClassUID=cls;ds.SOPInstanceUID=generate_uid();ds.SeriesInstanceUID=generate_uid();ds.StudyInstanceUID=study
        ds.PatientName='SYNTHETIC^PRIVATE';ds.PatientID='SYNTHETIC_PRIVATE_ID';ds.PatientBirthDate='19800101';ds.add_new(0x00110010,'LO','PRIVATE_TEXT');ds.StudyDescription='PRIVATE_DESCRIPTION'
        return ds
    rs=make('RTSTRUCT',RTStructureSetStorage);f=Dataset();f.FrameOfReferenceUID=frame;rs.ReferencedFrameOfReferenceSequence=[f]
    roi=Dataset();roi.ROINumber=1;roi.ROIName='PTV PRIVATE_NAME';roi.ReferencedFrameOfReferenceUID=frame;rs.StructureSetROISequence=[roi]
    planes=Dataset();planes.PixelSpacing=[.5,.5];planes.SpacingBetweenSlices=.75;planes.Rows=8;planes.Columns=9;planes.NumberOfFrames=10
    planes.ImagePositionPatient=[-4,3,2];planes.ImageOrientationPatient=[1,0,0,0,.8660254,.5]
    contour_roi=Dataset();contour_roi.ReferencedROINumber=1;contour_roi.add_new(0x3006004a,'SQ',[planes]);rs.ROIContourSequence=[contour_roi]
    rd=make('RTDOSE',RTDoseStorage);rd.FrameOfReferenceUID=frame;rd.Rows=2;rd.Columns=2;rd.NumberOfFrames=2;rd.BitsAllocated=16;rd.BitsStored=16;rd.HighBit=15;rd.PixelRepresentation=0;rd.SamplesPerPixel=1;rd.PhotometricInterpretation='MONOCHROME2';rd.PixelData=bytes(range(16));rd.DoseGridScaling='0.001';rd.DoseUnits='GY';rd.DoseType='PHYSICAL';rd.DoseSummationType='PLAN';rd.ImagePositionPatient=[1,2,3];rd.ImageOrientationPatient=[1,0,0,0,1,0];rd.PixelSpacing=[1,1];rd.GridFrameOffsetVector=[0,1]
    rs['ReferencedFrameOfReferenceSequence'].is_undefined_length=True
    rs.ReferencedFrameOfReferenceSequence[0].is_undefined_length_sequence_item=True
    return rs,rd

@pytest.mark.parametrize('implicit',[False,True])
def test_roundtrip(implicit):
    rs,rd=synthetic_pair(implicit);frame=rd.FrameOfReferenceUID
    result=bridge(binary(rs),binary(rd));assert_pair(rs,rd,result)
    assert result['structure'].StructureSetROISequence[0].ROIName=='PTV 1'
    # Real backend export after a SECOND server UID remapping: restore original
    # local linkage while retaining all full-resolution quantitative uint32 data.
    import numpy as np
    from upload import anonymize_dataset
    from dose_export import export_prediction
    from geometry import Grid
    server_source=anonymize_dataset(result['structure'],{})
    grid=Grid(np.array([-2.5,4.25,9.]),(3,4,5),.5)
    pred=np.arange(60,dtype=np.float32).reshape(grid.shape)/7
    exported=export_prediction(pred,grid,server_source,[1],20)
    server_dose=pydicom.dcmread(io.BytesIO(exported))
    restored=bridge(binary(rs),binary(rd),exported)['restored']
    assert restored.StudyInstanceUID==rs.StudyInstanceUID!=server_dose.StudyInstanceUID
    assert restored.FrameOfReferenceUID==frame!=server_dose.FrameOfReferenceUID
    assert restored.PatientName==rs.PatientName
    assert restored.SOPInstanceUID==server_dose.SOPInstanceUID
    assert restored.SeriesInstanceUID==server_dose.SeriesInstanceUID
    assert restored.BitsAllocated==32 and restored.pixel_array.shape==grid.shape
    assert restored.PixelData==server_dose.PixelData
    for field in ('DoseGridScaling','ImagePositionPatient','ImageOrientationPatient','GridFrameOffsetVector','PixelSpacing','DoseComment','DerivationDescription'):
        assert getattr(restored,field)==getattr(server_dose,field)
    for key in ('structure','dose'):
        payload=binary(result[key])
        assert b'PRIVATE' not in payload
        assert str(rs.StudyInstanceUID).encode() not in payload
        assert str(frame).encode() not in payload
    bad=pydicom.dcmread(io.BytesIO(binary(rd)));bad.PixelData+=b'PRIVATE_TRAILER'
    rejected(binary(rs),binary(bad))
    bad=pydicom.dcmread(io.BytesIO(binary(rs)));bad.add_new(0x7fe00010,'OB',b'PRIVATE_PIXELS')
    rejected(binary(bad),binary(rd))
    bad=bytearray(binary(rs));offset=bad.find(str(ExplicitVRLittleEndian if not implicit else ImplicitVRLittleEndian).encode());assert offset>0
    bad[offset:offset+len(str(ExplicitVRLittleEndian if not implicit else ImplicitVRLittleEndian))]=b'9'*len(str(ExplicitVRLittleEndian if not implicit else ImplicitVRLittleEndian))
    rejected(bytes(bad),binary(rd))

@pytest.mark.parametrize('payload',[b'',b'X'*132,b'\0'*128+b'DICM'])
def test_bad_part10_is_rejected(payload):
    p=subprocess.run(['node',str(Path(__file__).with_name('browser_privacy_bridge.cjs'))],input=gzip.compress(json.dumps({'structure':base64.b64encode(payload).decode()}).encode()),capture_output=True)
    assert p.returncode!=0
    assert not p.stdout
    assert p.stderr==b'PRIVACY_PARSE_FAILED'

@pytest.mark.skipif(os.environ.get('DOSE_ATLAS_REAL_PRIVACY_TEST')!='1',reason='Explicit offline real-data opt-in required')
def test_ten_local_cases():
    from local_cases import case_paths
    for number in range(1,11):
        paths=case_paths(f'C{number:02d}')
        rs=paths['structure'].read_bytes();rd=paths['dose'].read_bytes()
        assert_pair(pydicom.dcmread(io.BytesIO(rs)),pydicom.dcmread(io.BytesIO(rd)),bridge(rs,rd))
        print(f'Offline privacy case C{number:02d} passed',flush=True)

@pytest.mark.skipif(not os.environ.get('DOSE_ATLAS_HDSS_PRIVACY_DIR'),reason='Explicit offline HDSS directory opt-in required')
def test_hdss_folder_roundtrip():
    """Select actual modalities, never infer a file's role from its name."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        candidates={'RTSTRUCT':[],'RTDOSE':[]}
        for path in Path(os.environ['DOSE_ATLAS_HDSS_PRIVACY_DIR']).iterdir():
            if not path.is_file():continue
            try:ds=pydicom.dcmread(path,stop_before_pixels=True)
            except Exception:continue
            if ds.get('Modality') in candidates:candidates[ds.Modality].append(path)
        assert all(len(v)==1 for v in candidates.values()),'Expected one structure and one dose'
        rs=candidates['RTSTRUCT'][0].read_bytes();rd=candidates['RTDOSE'][0].read_bytes()
        source_rs=pydicom.dcmread(io.BytesIO(rs));source_rd=pydicom.dcmread(io.BytesIO(rd))
        assert_pair(source_rs,source_rd,bridge(rs,rd))


@pytest.mark.skipif(os.environ.get('DOSE_ATLAS_BROWSER_TEST')!='1',reason='Explicit local Playwright integration opt-in required')
def test_actual_app_network_and_original_export():
    rs,rd=synthetic_pair()
    anonymous=bridge(binary(rs),binary(rd))
    predicted=anonymous['dose']
    data={k:base64.b64encode(binary(v)).decode() for k,v in [('structure',rs),('dose',rd),('predicted',predicted)]}
    data['forbidden']=['PRIVATE',str(rs.SOPInstanceUID),str(rs.StudyInstanceUID),str(rs.SeriesInstanceUID),str(rd.SOPInstanceUID),str(rd.SeriesInstanceUID),str(rd.FrameOfReferenceUID)]
    run=subprocess.run(['node',str(Path(__file__).with_name('privacy_network.cjs'))],input=gzip.compress(json.dumps(data).encode()),capture_output=True,timeout=90)
    assert run.returncode==0,run.stderr.decode(errors='replace')
    result=json.loads(gzip.decompress(run.stdout))
    restored=pydicom.dcmread(io.BytesIO(base64.b64decode(result['restored'])))
    assert restored.PatientID==rs.PatientID and restored.PatientName==rs.PatientName
    assert restored.StudyInstanceUID==rs.StudyInstanceUID
    assert restored.FrameOfReferenceUID==rd.FrameOfReferenceUID
    assert restored.ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID==rs.SOPInstanceUID
    assert restored.SOPInstanceUID==predicted.SOPInstanceUID!=rd.SOPInstanceUID
    assert restored.SeriesInstanceUID==predicted.SeriesInstanceUID!=rd.SeriesInstanceUID
    assert restored.PixelData==predicted.PixelData
    assert result['uploads']>=2 and result['downloadRequests']==1
