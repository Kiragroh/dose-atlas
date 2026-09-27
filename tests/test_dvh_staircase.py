import numpy as np
from metrics import dvh, dose_metrics


def test_tied_values_are_vertical_steps_without_artificial_dose_bins():
    values=np.array([19.991,19.991,20.003,21.])
    curve=dvh(values,20)
    assert curve==[[0.,100.],[19.991,100.],[19.991,50.],
                  [20.003,50.],[20.003,25.],[21.,25.],[21.,0.],[40.,0.]]
    assert dose_metrics(values,20)['Dmean_Gy']==np.mean(values)


def test_equal_doses_drop_at_the_exact_value():
    assert dvh(np.full(500,22.345678),20)==[
        [0.,100.],[22.345678,100.],[22.345678,0.],[40.,0.]]


def test_large_curve_is_bounded_monotone_and_exact_at_retained_knots():
    values=np.linspace(1,37,50001)
    curve=np.array(dvh(values,20))
    assert len(curve)<=404
    assert np.all(np.diff(curve[:,0])>=0)
    assert np.all(np.diff(curve[:,1])<=0)
    for a,b in zip(curve[1:-1:2],curve[2:-1:2]):
        assert a[0]==b[0]
        assert abs(a[1]-100*np.mean(values>=a[0]))<1e-6
        assert abs(b[1]-100*np.mean(values>a[0]))<1e-6
    # Between knots the omitted cumulative volume is at most one rank interval.
    assert max(curve[2:-3:2,1]-curve[3:-2:2,1])<=.501


def test_many_ties_survive_compression():
    values=np.r_[np.linspace(0,20,50000),np.full(50000,10.001)]
    curve=dvh(values,20)
    knot=[p[1] for p in curve if p[0]==10.001]
    assert len(knot)==2 and abs(knot[0]-knot[1]-50)<1e-6


def test_invalid_or_empty_dvh_is_absent():
    assert dvh([],20) is None
    assert dvh([1,np.nan],20) is None
