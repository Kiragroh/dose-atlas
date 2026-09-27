"""Synthetic tests for explicitly exploratory, target-specific Rx scaling."""
import copy
from io import BytesIO
import numpy as np
import pydicom
import pytest
import app as api
from geometry import InputError
from model import prescription_values, scale_prescriptions
from upload import preferred_targets, prescription_hypotheses, inspect_upload
from test_api import structure_bytes
from test_upload import ptv
from test_geometry import dose_dataset


def two_targets():
    _,ds=structure_bytes()
    region=copy.deepcopy(ds.StructureSetROISequence[0]);region.ROINumber=7;region.ROIName='PTV other'
    contour=copy.deepcopy(ds.ROIContourSequence[0]);contour.ReferencedROINumber=7
    for c in contour.ContourSequence:
        points=np.asarray(c.ContourData).reshape(-1,3);points[:,0]+=15;c.ContourData=points.ravel().tolist()
    ds.StructureSetROISequence.append(region);ds.ROIContourSequence.append(contour)
    stream=BytesIO();ds.save_as(stream,write_like_original=False)
    return stream.getvalue()


def test_uniform_scaling_is_bitwise_legacy_and_mixed_is_exact_inside_targets():
    labels=np.array([[[1,0,0,0,2]]],np.int16)
    relative=np.array([[[1.1,.7,.3,.8,1.2]]],np.float32)
    np.testing.assert_array_equal(scale_prescriptions(relative,labels,[20.,20.],1),relative*20)
    scaled=scale_prescriptions(relative,labels,[10.,30.],1)
    assert scaled[0,0,0]==relative[0,0,0]*10
    assert scaled[0,0,4]==relative[0,0,4]*30
    # Exact independent two-target inverse-square distances: 1 vs3, 2 vs2.
    assert scaled[0,0,1]==pytest.approx(relative[0,0,1]*12)
    assert scaled[0,0,2]==pytest.approx(relative[0,0,2]*20)
    np.testing.assert_array_equal(relative,np.array([[[1.1,.7,.3,.8,1.2]]],np.float32))


@pytest.mark.parametrize('overrides',[{'2':20},{'01':20},{1:20},{'1':True},{'1':'20'},{'1':0},{'1':101},{'1':float('nan')},[]])
def test_invalid_mapping_is_rejected(overrides):
    with pytest.raises(InputError):prescription_values([1],20,overrides)


def test_partial_map_fallback_and_technical_range():
    assert prescription_values([1,7],19,{'7':12})==[19,12]
    for rx in (0,101,float('inf'),True):
        with pytest.raises(InputError):prescription_values([1],rx)
    assert prescription_values([1],5)==[5]


def test_mixed_result_own_rx_metrics_metadata_export_and_extrapolation(monkeypatch):
    monkeypatch.setattr(api,'predict',lambda model,X:np.ones(len(X),np.float32))
    monkeypatch.setattr(api,'load_model',lambda:None)
    result,bundle=api.compute(two_targets(),None,'[1,7]',20,1,True,True,'{"1":12,"7":24}')
    summary=result['summary']
    assert summary['prescription_Gy']==20
    assert summary['target_prescriptions']=={'1':12.,'7':24.}
    assert summary['prescription_range_Gy']==[12.,24.] and summary['mixed_prescriptions']
    assert summary['predicted']['Paddick_CI'] is None and summary['predicted']['GI'] is None
    assert summary['predicted']['V12_domain_cc']>0
    for row,rx in zip(result['metrics'],[12,24]):
        assert row['prescription_Gy']==rx and row['predicted']['Dmean_Gy']==rx
        assert row['predicted']['V100_pct']==100
    assert [row['prescription_Gy'] for row in result['dvhs']]==[12,24]
    assert [row['prescription_Gy'] for row in result['ring_benchmark']['rows']]==[12,24]
    assert any('Extrapolation' in w for w in result['warnings'])
    assert any('nicht mit heterogenen' in w for w in result['warnings'])
    rd=pydicom.dcmread(BytesIO(bundle['prediction.dcm']))
    rs=pydicom.dcmread(BytesIO(bundle['structure.dcm']))
    assert rd.FrameOfReferenceUID==rs.StructureSetROISequence[0].ReferencedFrameOfReferenceUID
    assert 'heterogeneous target Rx' in rd.DerivationDescription
    assert 'ROI 1: 12 Gy' in rd.DerivationDescription and 'ROI 7: 24 Gy' in rd.DerivationDescription
    assert 'input Rx 20' not in rd.DerivationDescription


def test_auto_ptv_first_else_gtv_and_gtv_proposal_rule():
    rois=ptv();rois[7]=dict(rois[1],name='GTV 7',number=7)
    assert preferred_targets(rois)==[1]
    del rois[1]
    assert preferred_targets(rois)==[7]
    dose=dose_dataset();dose.PixelData=np.full((3,3,4),1820,np.uint16).tobytes()
    row=prescription_hypotheses(rois,dose)[0]
    assert row['number']==7 and row['eligible'] and row['suggested_Gy']==19
    _,ds=structure_bytes();ds.StructureSetROISequence[0].ROIName='GTV demo'
    stream=BytesIO();ds.save_as(stream,write_like_original=False)
    info=inspect_upload(stream.getvalue())
    assert info['suggested_targets']==[1] and info['target_kind']=='GTV'


def test_prediction_form_accepts_per_target_mapping(monkeypatch):
    from fastapi.testclient import TestClient
    import storage
    monkeypatch.setattr(storage,'config_status',lambda:{'enabled':False})
    monkeypatch.setattr(api,'predict',lambda model,X:np.ones(len(X),np.float32))
    monkeypatch.setattr(api,'load_model',lambda:None)
    data=two_targets()
    with TestClient(api.app) as client:
        result=client.post('/api/predict',files={'structure':('synthetic.dcm',data)},
            data={'targets':'[1,7]','prescription':'20','target_prescriptions':'{"1":12,"7":24}'})
        assert result.status_code==200,result.text[:100]
        assert result.json()['summary']['target_prescriptions']=={'1':12,'7':24}
        rejected=client.post('/api/predict',files={'structure':('synthetic.dcm',data)},
            data={'targets':'[1,7]','prescription':'20','target_prescriptions':'{"999":24}'})
        assert rejected.status_code==422


def test_mixed_organ_relative_threshold_is_undefined():
    from geometry import Grid
    from test_geometry import square
    grid=Grid(np.array([-5.,-5.,-5.]),(11,11,11))
    labels=np.zeros(grid.shape,np.int16);labels[5,5,2]=1;labels[5,5,8]=2
    contours=[square(-1,1),square(1,1)]
    rois={1:dict(name='PTV 1',contours=contours),2:dict(name='PTV 2',contours=contours),
          3:dict(name='Brainstem',contours=contours)}
    stats=[dict(volume_cc=.001),dict(volume_cc=.001)]
    X=np.zeros((labels.size,8));dose=np.full(grid.shape,20.)
    result=api.response_data(rois,[1,2],20,grid,X,labels,stats,dose,dose,prescriptions=[12,24])
    organ=next(row for row in result['metrics'] if row['role']=='organ')
    assert organ['prescription_Gy'] is None
    assert organ['predicted']['V100_pct'] is None and organ['reference']['V100_pct'] is None
    assert organ['predicted']['Dmean_Gy']==20
    assert next(row for row in result['dvhs'] if row['roi']=='Brainstem')['prescription_Gy'] is None
