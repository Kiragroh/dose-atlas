"""Synthetic source-plane geometry; no clinical files or reference identifiers."""
import numpy as np
import pytest
from pydicom.dataset import Dataset
from geometry import Grid, InputError, rasterize, roi_bounds, structure_rois


def source_structure(levels=(0, 2), angle=0., frames=3, step=2., origin=(10., -3., 8.)):
    c, s = np.cos(angle), np.sin(angle)
    origin = np.array(origin)
    u, v, normal = np.array([c, 0., -s]), np.array([0., 1., 0.]), np.array([s, 0., c])
    source = Dataset()
    source.ImageOrientationPatient = [*u, *v]
    source.ImagePositionPatient = origin.tolist()
    source.PixelSpacing = [.5, .7]
    source.SpacingBetweenSlices = step
    source.NumberOfFrames = frames
    source.Rows = 40
    source.Columns = 40
    region = Dataset()
    region.ROINumber = 1
    region.ROIName = 'Synthetic target'
    region.ReferencedFrameOfReferenceUID = '1.2.3'
    contour_region = Dataset()
    contour_region.ReferencedROINumber = 1
    contour_region.SourcePixelPlanesCharacteristicsSequence = [source]
    contour_region.ContourSequence = []
    for level in levels:
        corners = np.array([[-1.3,-2.2],[2.7,-2.2],[2.7,.8],[-1.3,.8]])
        points = origin + corners[:,0,None]*u + corners[:,1,None]*v + level*step*normal
        contour = Dataset()
        contour.ContourGeometricType = 'CLOSED_PLANAR'
        contour.NumberOfContourPoints = 4
        contour.ContourData = points.ravel().tolist()
        contour_region.ContourSequence.append(contour)
    ds = Dataset()
    ds.StructureSetROISequence = [region]
    ds.ROIContourSequence = [contour_region]
    return ds


def roi(ds):
    return structure_rois(ds)[1]


def sample(region, point):
    return rasterize(region, Grid(np.asarray(point,float), (1,1,1))).item()


def test_oblique_voxels_match_independent_world_coordinate_box_and_slabs():
    angle = np.pi/6
    origin = np.array([10., -3., 8.])
    region = roi(source_structure(angle=angle))
    grid = Grid(np.array([5.13,-6.17,3.21]), (26,18,26), .5)
    world = grid.points() - origin
    # Explicit inverse rotation, independently of the parser's stored basis.
    a = world[:,0]*np.cos(angle) - world[:,2]*np.sin(angle)
    b = world[:,1]
    depth = world[:,0]*np.sin(angle) + world[:,2]*np.cos(angle)
    expected = ((a > -1.3) & (a < 2.7) & (b > -2.2) & (b < .8) &
                (((depth >= -1) & (depth < 1)) | ((depth >= 3) & (depth < 5))))
    actual = rasterize(region,grid)
    assert expected.any() and not expected.all()
    np.testing.assert_array_equal(actual, expected.reshape(grid.shape))


def test_absent_native_plane_remains_empty_instead_of_bridging_gap():
    region = roi(source_structure())
    for offset in (-.999,0,.999,3.,4.,4.999):
        assert sample(region,[10,-3,8+offset])
    for offset in (-1.001,1.,2.,2.999,5.,5.001):
        assert not sample(region,[10,-3,8+offset])


def test_single_native_plane_has_explicit_half_slice_thickness():
    region = roi(source_structure(levels=(0,),frames=1,step=3.))
    low,high = roi_bounds(region)
    np.testing.assert_allclose(low,[8.7,-5.2,6.5])
    np.testing.assert_allclose(high,[12.7,-2.2,9.5])
    assert sample(region,[10,-3,6.5])
    assert sample(region,[10,-3,9.499])
    assert not sample(region,[10,-3,6.499])
    assert not sample(region,[10,-3,9.5])


def test_oblique_bounds_include_endpoint_slabs_along_normal():
    angle=np.pi/4
    region=roi(source_structure(levels=(0,),frames=1,angle=angle,step=4.))
    points=np.concatenate(region['contours'])
    low,high=roi_bounds(region)
    # Slab thickness extends both world x and z; no extension along y.
    half=np.array([np.sqrt(2),0,np.sqrt(2)])
    np.testing.assert_allclose(low,points.min(0)-half)
    np.testing.assert_allclose(high,points.max(0)+half)


@pytest.mark.parametrize('offset',[-.03,.03,2.,100.])
def test_contour_outside_declared_native_plane_rejected(offset):
    ds=source_structure(levels=(0,),frames=1)
    contour=ds.ROIContourSequence[0].ContourSequence[0]
    points=np.asarray(contour.ContourData,float).reshape(-1,3)
    points[:,2]+=offset
    contour.ContourData=points.ravel().tolist()
    with pytest.raises(InputError,match='HDSS'):
        structure_rois(ds)


def test_nonplanar_contour_is_not_accepted_by_mean_plane_only():
    ds=source_structure(levels=(0,),frames=1,angle=np.pi/4)
    contour=ds.ROIContourSequence[0].ContourSequence[0]
    points=np.asarray(contour.ContourData,float).reshape(-1,3)
    normal=np.array([np.sqrt(.5),0,np.sqrt(.5)])
    points[0]+=.04*normal
    points[1]-=.04*normal
    contour.ContourData=points.ravel().tolist()
    with pytest.raises(InputError,match='HDSS'):
        structure_rois(ds)


@pytest.mark.parametrize('field,value',[
    ('SpacingBetweenSlices',0),('NumberOfFrames',0),('PixelSpacing',[0,1]),
    ('Rows',0),('ImageOrientationPatient',[1,0,0,1,0,0]),
    ('ImagePositionPatient',[0,0]),
])
def test_invalid_source_geometry_rejected(field,value):
    ds=source_structure()
    setattr(ds.ROIContourSequence[0].SourcePixelPlanesCharacteristicsSequence[0],field,value)
    with pytest.raises(InputError,match='HDSS'):
        structure_rois(ds)


def test_grid_includes_explicit_thick_source_slabs():
    from geometry import make_grid
    ds=source_structure(levels=(0,), frames=1, step=100., angle=np.pi/4)
    rois=structure_rois(ds)
    low,high=roi_bounds(rois[1])
    grid=make_grid(rois,[1],padding=0)
    assert np.all(grid.origin <= low)
    assert np.all(np.array([a[-1] for a in grid.axes]) >= high)


def test_repeated_anonymization_retains_source_geometry_and_brain_class():
    from upload import anonymize_dataset, safe_roi_name
    ds=source_structure(angle=np.pi/6)
    first=anonymize_dataset(ds,{})
    second=anonymize_dataset(first,{})
    a=structure_rois(ds)[1]['source_planes']
    b=structure_rois(second)[1]['source_planes']
    for field in ('origin','basis','spacing','frames'):
        np.testing.assert_array_equal(a[field],b[field])
    assert safe_roi_name(safe_roi_name('Brain',15),15)=='Brain 15'
