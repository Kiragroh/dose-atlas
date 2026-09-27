import numpy as np
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian
from geometry import Grid, rasterize, sample_dose, InputError, make_grid, roi_bounds
from metrics import dose_metrics, global_metrics


def square(z,a=2):
    return np.array([[-a,-a,z],[a,-a,z],[a,a,z],[-a,a,z]],float)


def dose_dataset(offsets=(0,2,4),origin=(0,0,0),orientation=(1,0,0,0,1,0)):
    ds=Dataset();ds.file_meta=FileMetaDataset();ds.file_meta.TransferSyntaxUID=ExplicitVRLittleEndian
    ds.Modality='RTDOSE';ds.FrameOfReferenceUID='1.2.3';ds.DoseUnits='GY';ds.DoseType='PHYSICAL';ds.DoseSummationType='PLAN'
    ds.ImageOrientationPatient=list(orientation);ds.ImagePositionPatient=list(origin)
    ds.GridFrameOffsetVector=list(offsets);ds.PixelSpacing=[2,3];ds.DoseGridScaling=.01
    ds.Rows=3;ds.Columns=4;ds.NumberOfFrames=3;ds.SamplesPerPixel=1
    ds.PhotometricInterpretation='MONOCHROME2';ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=0
    z,y,x=np.indices((3,3,4));a=(100*(z*2+y*4+x*9)).astype(np.uint16)
    ds.PixelData=a.tobytes();return ds


def test_dose_physical_coordinates_and_spacing():
    # Dose = z + 2y + 3x on physical axes, including anisotropic spacing.
    ds=dose_dataset();g=Grid(np.array([1.5,1,1]),(1,1,1))
    assert sample_dose(ds,'1.2.3',g).item()==pytest.approx(7.5)


def test_rotated_dose_orientation():
    ds=dose_dataset(orientation=(0,1,0,-1,0,0))
    # local x=1.5 y=1 z=1 -> world (-1,1.5,1)
    g=Grid(np.array([-1,1.5,1]),(1,1,1))
    assert sample_dose(ds,'1.2.3',g).item()==pytest.approx(7.5)


def test_absolute_offsets_and_out_of_field():
    ds=dose_dataset(offsets=(10,12,14),origin=(0,0,10))
    assert sample_dose(ds,'1.2.3',Grid(np.array([0,0,11]),(1,1,1))).item()==pytest.approx(1)
    assert np.isnan(sample_dose(ds,'1.2.3',Grid(np.array([99,0,11]),(1,1,1))).item())


def test_descending_and_nonuniform_offsets():
    ds=dose_dataset(offsets=(0,-2,-5))
    assert sample_dose(ds,'1.2.3',Grid(np.array([0,0,-3.5]),(1,1,1))).item()==pytest.approx(3)


def test_frame_mismatch_and_relative_dose_rejected():
    ds=dose_dataset()
    with pytest.raises(InputError):sample_dose(ds,'1.2.4',Grid(np.zeros(3),(1,1,1)))
    ds.DoseUnits='RELATIVE'
    with pytest.raises(InputError):sample_dose(ds,'1.2.3',Grid(np.zeros(3),(1,1,1)))


def test_polygon_keeps_final_vertex_and_hole():
    g=Grid(np.array([-3.25,-3.25,-1]),(4,8,8))
    solid=rasterize({'contours':[square(0),square(1)]},g)
    hole=rasterize({'contours':[square(0),square(0,1),square(1),square(1,1)]},g)
    assert solid.sum()==32
    assert hole.sum()==24
    assert np.all(hole<=solid)


def test_single_plane_and_memory_limit():
    with pytest.raises(InputError):rasterize({'contours':[square(0)]},Grid(np.zeros(3),(5,5,5)))
    with pytest.raises(InputError):make_grid({1:{'contours':[square(0),square(1)]}},[1],max_voxels=2)


def test_metrics_analytic():
    m=dose_metrics(np.array([10.,20.,30.]),20)
    assert m['Dmean_Gy']==20 and m['V100_pct']==pytest.approx(200/3)
    assert dose_metrics([np.nan],20) is None
    dose=np.array([20.,20.,10.,0.]);labels=np.array([1,1,0,0]);domain=np.ones(4,bool)
    m=global_metrics(dose,labels,domain,20,.001)
    assert m['Paddick_CI']==1 and m['GI']==1.5


def test_decoded_shape_validated_before_pixel_access():
    ds=dose_dataset();ds.NumberOfFrames=100000000
    with pytest.raises(InputError,match='Dimensionen|dimensionen'):
        sample_dose(ds,'1.2.3',Grid(np.zeros(3),(1,1,1)))


def test_organ_bounds_include_endpoint_slabs():
    contours=[square(3.9),square(8.9)]
    for contour in contours:contour[1:3,2]+=.001
    low,high=roi_bounds({'contours':contours})
    assert low[2]==pytest.approx(1.4)
    assert high[2]==pytest.approx(11.4)
