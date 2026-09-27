import numpy as np
from geometry import display_polyline, display_target_contours, structure_rois
from test_hdss_geometry import source_structure


def test_closed_native_curve_preserved_with_bounded_error():
    a=np.linspace(0,2*np.pi,1000,endpoint=False)
    p=np.column_stack((10*np.cos(a),10*np.sin(a),np.full(len(a),2.)))
    q=display_polyline(p)
    assert len(q)>48
    np.testing.assert_array_equal(q[0],q[-1])
    distances=[]
    for x in p:
        v=q[1:]-q[:-1]
        t=np.clip(np.sum((x-q[:-1])*v,axis=1)/np.sum(v*v,axis=1),0,1)
        distances.append(np.linalg.norm(x-(q[:-1]+t[:,None]*v),axis=1).min())
    assert max(distances)<=.010001


def test_axial_display_uses_native_vertices_not_model_voxel_edges():
    a=np.linspace(0,2*np.pi,150,endpoint=False)
    loops=[np.column_stack((.37+3*np.cos(a),.21+3*np.sin(a),np.full(len(a),z))) for z in [0.,1.]]
    roi=dict(contours=loops)
    paths,step=display_target_contours({1:roi},[1],[.25,5.])
    assert paths[1]==[]
    q=np.array(paths[0][0])
    assert np.max(abs(np.linalg.norm(q[:,:2]-[.37,.21],axis=1)-3))<.0001
    assert np.all(q[:,2]==.25)
    assert step==.2


def test_oblique_display_contains_only_actual_slabs_and_is_finer_than_dose_grid():
    roi=structure_rois(source_structure(levels=(0,2),angle=np.pi/6))[1]
    original=[c.copy() for c in roi['contours']]
    paths,step=display_target_contours({1:roi},[1],np.arange(5.,16.,.25))
    assert any(paths) and step==.2
    for c,before in zip(roi['contours'],original):np.testing.assert_array_equal(c,before)
    for z,loops in zip(np.arange(5.,16.,.25),paths):
        for loop in loops:
            p=np.array(loop);assert np.all(p[:,2]==z)
            np.testing.assert_array_equal(p[0],p[-1])


def test_full_grid_isodose_preserves_peak_lost_by_coarse_preview():
    from geometry import Grid,display_isodoses
    grid=Grid(np.zeros(3),(1,3,3))
    dose=np.zeros(grid.shape);dose[0,1,1]=2
    paths=display_isodoses(dose,grid,[0],np.ones(grid.shape,bool),levels=(1,))
    assert paths[0]['1']
    assert not dose[0,::2,::2].any()
    np.testing.assert_array_equal(paths[0]['1'][0][0],paths[0]['1'][0][-1])


def test_open_isodose_is_never_closed_across_missing_support():
    from geometry import Grid,display_isodoses
    grid=Grid(np.zeros(3),(1,3,3))
    dose=np.broadcast_to(np.arange(3),(1,3,3)).copy()
    paths=display_isodoses(dose,grid,[0],np.ones(grid.shape,bool),levels=(.5,))
    line=np.array(paths[0]['0.5'][0])
    assert not np.array_equal(line[0],line[-1])
    assert np.all(line[:,0]==.5)
