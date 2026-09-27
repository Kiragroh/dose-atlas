import numpy as np
import pytest
from metrics import local_target_metrics
from storage import compact_result


def test_exact_counts_inverse_and_gradient():
    labels=np.array([[[1,1,0,0,0]]])
    dose=np.array([[[20.,20.,20.,12.,5.]]])
    rows,means=local_target_metrics(dose,dose,labels,np.ones_like(labels,bool),[20],1)
    m=rows[0]['predicted']
    assert m['local_Paddick_CI']==pytest.approx(2/3)
    assert m['local_inverse_CI']==pytest.approx(1.5)
    assert m['local_GI']==pytest.approx(4/3)
    assert m['local_V12_cc']==pytest.approx(.002)
    assert means['predicted']['mean_local_inverse_CI']==pytest.approx(1.5)


def test_disjoint_territories_and_mixed_rx():
    labels=np.array([[[1,0,0,0,0,0,2]]])
    dose=np.array([[[20.,15.,12.,12.,12.,15.,30.]]])
    rows,means=local_target_metrics(dose,None,labels,np.ones_like(labels,bool),[20,30],1)
    assert sum(r['predicted']['local_V12_cc'] for r in rows)==pytest.approx(.005)
    assert rows[0]['predicted']['local_GI']>rows[1]['predicted']['local_GI']
    assert means['reference'] is None
    assert means['predicted']['mean_local_GI_n']==2


def test_partial_support_invalidates_both_plans():
    labels=np.array([[[1,0,0,0,2]]]);domain=np.ones_like(labels,bool)
    pred=np.full(labels.shape,20.);ref=pred.copy();ref[0,0,0]=np.nan
    rows,means=local_target_metrics(pred,ref,labels,domain,[20,20],1)
    assert rows[0]['local_coverage_pct']<100
    assert all(v is None for v in rows[0]['predicted'].values())
    assert all(v is None for v in rows[0]['reference'].values())
    assert rows[1]['predicted']['local_GI']==1
    assert means['predicted']['mean_local_GI_n']==1


def test_no_rx_volume_and_zero_overlap():
    labels=np.array([[[1,0,0]]]);domain=np.ones_like(labels,bool)
    for dose in (np.array([[[0.,0.,0.]]]),np.array([[[0.,20.,20.]]])):
        rows,_=local_target_metrics(dose,None,labels,domain,[20],1)
        assert rows[0]['predicted']['local_inverse_CI'] is None


def test_mean_of_inverses_not_inverse_of_mean_and_archive():
    labels=np.array([[[1,0,0,0,2]]]);dose=np.array([[[20.,20.,0.,0.,20.]]])
    rows,means=local_target_metrics(dose,dose,labels,np.ones_like(labels,bool),[20,20],1)
    m=means['predicted']
    assert m['mean_local_inverse_CI']==pytest.approx(1.5)
    assert m['mean_local_inverse_CI']!=pytest.approx(1/m['mean_local_CI'])
    result={'summary':{'local_metrics_version':1,'predicted':m},'metrics':[dict(roi='private',role='target',**rows[0])]}
    saved=compact_result(result)
    assert saved['summary']['local_metrics_version']==1
    assert saved['summary']['predicted']==m
    assert saved['metrics'][0]['predicted']==rows[0]['predicted']
    assert saved['metrics'][0]['local_coverage_pct']==100


def test_spacing_cubic_volume_and_restricted_domain():
    labels=np.array([[[1,0,0,0]]]);dose=np.full(labels.shape,20.)
    domain=np.array([[[True,True,False,False]]])
    rows,_=local_target_metrics(dose,None,labels,domain,[20],2)
    assert rows[0]['predicted']['local_V12_cc']==pytest.approx(.008)
    assert rows[0]['local_region_cc']==pytest.approx(.016)


def test_paired_means_use_identical_targets_for_undefined_indices():
    labels=np.array([[[1,0,0,0,2]]]);pred=np.full(labels.shape,20.);ref=pred.copy();ref[0,0,0]=0
    rows,means=local_target_metrics(pred,ref,labels,np.ones_like(labels,bool),[20,20],1)
    assert rows[0]['predicted']['local_inverse_CI'] is not None
    assert rows[0]['reference']['local_inverse_CI'] is None
    assert means['predicted']['mean_local_inverse_CI_n']==means['reference']['mean_local_inverse_CI_n']==1
    assert means['predicted']['mean_local_inverse_CI']==means['reference']['mean_local_inverse_CI']
