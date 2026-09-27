import numpy as np
from metrics import ring_auc,ring_benchmark


def test_exact_auc_and_prescription_scaling():
    # Four voxels: below, on lower boundary, halfway, above upper boundary.
    values=np.array([2,10,13,18.])
    assert np.isclose(ring_auc(values,20,.001),.00045)
    assert np.isclose(ring_auc(values*2,40,.001),.00045)


def test_identity_direction_and_target_exclusion():
    labels=np.zeros((31,31,31),int)
    labels[15,15,15]=1;labels[15,15,18]=2
    domain=np.ones_like(labels,bool)
    pred=np.full(labels.shape,14.);ref=np.full(labels.shape,12.)
    result=ring_benchmark(pred,ref,labels,domain,20,1,['A','B'])
    for r in result['rows']: assert np.isclose(r['ratio'],2)
    # Dose inside both targets cannot affect either ring's AUC.
    ref[labels>0]=500
    changed=ring_benchmark(pred,ref,labels,domain,20,1,['A','B'])
    assert [r['ratio'] for r in changed['rows']]==[r['ratio'] for r in result['rows']]
    identical=ring_benchmark(pred,pred,labels,domain,20,1,['A','B'])
    assert all(r['ratio']==1 for r in identical['rows'])


def test_missing_support_and_zero_denominator_never_score():
    labels=np.zeros((31,31,31),int);labels[15,15,15]=1
    domain=np.ones_like(labels,bool);pred=np.full(labels.shape,14.);ref=pred.copy()
    ref[15,15,16]=np.nan
    row=ring_benchmark(pred,ref,labels,domain,20,1,['A'])['rows'][0]
    assert row['ratio'] is None and row['coverage_pct']<100
    assert row['predicted_auc_cc'] is None
    ref[:]=2
    assert ring_benchmark(pred,ref,labels,domain,20,1,['A'])['rows'][0]['ratio'] is None
    assert ring_benchmark(pred,None,labels,domain,20,1,['A'])['rows'][0]['ratio'] is None


def test_grid_edge_ring_is_rejected():
    labels=np.zeros((10,10,10),int);labels[1,1,1]=1
    dose=np.full(labels.shape,14.)
    row=ring_benchmark(dose,dose,labels,np.ones_like(labels,bool),20,1,['A'])['rows'][0]
    assert row['ratio'] is None and 'Rasterrand' in row['reason']
