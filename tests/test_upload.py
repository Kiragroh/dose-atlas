import math
import numpy as np
import pytest
from pydicom.dataset import Dataset
from upload import anonymize_dataset,prescription_hypotheses,inspect_upload
from geometry import read_dicom
from test_api import structure_bytes
from test_geometry import dose_dataset


def test_anonymization_removes_nested_identifiers_and_preserves_dose_geometry():
    _,rs=structure_bytes();rs.PatientName='SECRET^NAME';rs.PatientID='SECRET_ID';rs.PatientBirthDate='19700101'
    rs.InstitutionName='SECRET_HOSPITAL';rs.StudyInstanceUID='1.2.55'
    rs.StructureSetROISequence[0].ROIName='PTV SECRET_ID'
    rs.StructureSetROISequence[0].ROIDescription='SECRET_NOTE'
    rs.add_new((0x0011,0x1010),'LO','PRIVATE_SECRET')
    dose=dose_dataset();dose.SOPInstanceUID='1.2.99';dose.PatientName='SECRET^NAME'
    mapping={};clean_rs=anonymize_dataset(rs,mapping);clean_dose=anonymize_dataset(dose,mapping)
    assert clean_rs.PatientID=='ANON' and clean_rs.PatientIdentityRemoved=='YES'
    assert not hasattr(clean_rs,'PatientBirthDate') and not hasattr(clean_rs,'InstitutionName')
    assert not hasattr(clean_rs.StructureSetROISequence[0],'ROIDescription')
    assert 'SECRET' not in str(clean_rs)
    assert clean_rs.StructureSetROISequence[0].ROIName=='PTV 1'
    assert clean_rs.StructureSetROISequence[0].ReferencedFrameOfReferenceUID==clean_dose.FrameOfReferenceUID
    assert str(clean_dose.FrameOfReferenceUID)!='1.2.3'
    assert clean_rs.SOPClassUID==rs.SOPClassUID
    np.testing.assert_array_equal(clean_dose.pixel_array,dose.pixel_array)
    assert rs.PatientID=='SECRET_ID' # input unchanged


def ptv():
    contours=[np.array([[0.1,0.1,z],[2.9,.1,z],[2.9,2.9,z],[.1,2.9,z]]) for z in (1,2)]
    return {1:dict(number=1,name='PTV 01',frame='1.2.3',contours=contours)}


@pytest.mark.parametrize('gy,eligible,suggestion',[(10.,False,None),(10.01,True,11),(18.2,True,19),(20.,True,20)])
def test_mean_threshold_strict_and_d98_round_up(gy,eligible,suggestion):
    dose=dose_dataset();dose.DoseGridScaling=.01
    dose.PixelData=np.full((3,3,4),round(gy*100),np.uint16).tobytes()
    result=prescription_hypotheses(ptv(),dose)[0]
    assert result['eligible'] is eligible
    assert result['suggested_Gy']==suggestion
    assert result['D98_Gy']==pytest.approx(gy,abs=1e-5)


def test_each_ptv_gets_distinct_proposal_and_nonptv_excluded():
    dose=dose_dataset();values=np.zeros((3,3,4),np.uint16)
    values[:,:,:2]=1820;values[:,:,2:]=2030;dose.PixelData=values.tobytes()
    rois=ptv();second={**rois[1],'number':2,'name':'PTV 02',
                       'contours':[c+np.array([6.,0,0]) for c in rois[1]['contours']]}
    rois[2]=second;rois[3]={**second,'number':3,'name':'GTV 03'}
    results=prescription_hypotheses(rois,dose)
    assert [r['number'] for r in results]==[1,2]
    assert [r['suggested_Gy'] for r in results]==[19,21]


def test_frame_or_truncated_ptv_has_no_proposal():
    dose=dose_dataset();rois=ptv();rois[1]['frame']='1.2.4'
    row=prescription_hypotheses(rois,dose)[0]
    assert row['suggested_Gy'] is None and 'Referenzrahmen' in row['reason']
    rois=ptv();rois[1]['contours']=[c+np.array([20,0,0]) for c in rois[1]['contours']]
    assert prescription_hypotheses(rois,dose)[0]['suggested_Gy'] is None


def test_anonymization_choice_is_effective_not_cosmetic():
    raw,_=structure_bytes()
    cleaned=inspect_upload(raw,anonymize=True);unchanged=inspect_upload(raw,anonymize=False)
    assert cleaned['rois'][0]['name']=='PTV 1'
    assert unchanged['rois'][0]['name']=='PTV demo'
    assert cleaned['anonymization']['applied'] is True
    assert unchanged['anonymization']['applied'] is False
    assert cleaned['anonymization']['before_transmission'] is False


@pytest.mark.parametrize('raw,mean,suggested',[(21000,21.,21),(21001,21.001,22),(10000,10.,None),(10001,10.001,11)])
def test_milligray_scaling_does_not_round_up_integer_noise(raw,mean,suggested):
    dose=dose_dataset();dose.DoseGridScaling=.001
    dose.PixelData=np.full((3,3,4),raw,np.uint16).tobytes()
    result=prescription_hypotheses(ptv(),dose)[0]
    assert result['Dmean_Gy']==pytest.approx(mean,abs=1e-9)
    assert result['suggested_Gy']==suggested


def test_parser_warnings_do_not_echo_input_identifiers():
    import warnings
    raw,_=structure_bytes()
    raw=raw.replace(b'1.2.3.4',b'SECRET7')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        result=inspect_upload(raw,anonymize=True)
    assert result['anonymization']['applied']
    assert not any('SECRET7' in str(w.message) for w in caught)
    assert 'SECRET7' not in str(result)
