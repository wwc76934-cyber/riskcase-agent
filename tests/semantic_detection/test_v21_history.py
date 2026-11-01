"""Actual edge isolation, padding, active capacity controls and cold-start tests."""
import numpy as np
import pandas as pd
import pytest
import torch

from app.risk.neural_model import seed_training
from app.risk.semantic_detection.history_experiment import gather_history
from app.risk.semantic_detection.temporal import historical_neighbors
from app.risk.semantic_detection.v2.history import Config,Model,STAT_NAMES,deterministic_statistics,oldest_first
from app.risk.semantic_detection.v2.history_data import verify_neighbors


def metadata():
    return pd.DataFrame({'transaction_id':list('abcdef'),'user_id':['u','u','u','v','u','u'],
        'card_id':['0','1','0','0','0','0'],'event_time':['2017-01-01T00:00:00','2017-01-01T01:00:00',
        '2017-01-01T02:00:00','2017-01-01T03:00:00','2017-01-01T02:00:00','2017-01-01T04:00:00'],
        'split':['train']*6})


def test_every_joint_card_edge_and_equal_time_guard():
    meta=metadata();n,a=historical_neighbors(meta,k=6,group='user_card',training=True)
    proof=verify_neighbors(meta,n,a,training=True)
    assert proof['all_edges_strict_past_same_user_same_card']
    assert n[1,0]==-1 and n[3,0]==-1
    assert n[2,0]==0 and n[4,0]==0
    assert list(n[5,:3])==[4,2,0]
    bad=n.copy();bad[2,0]=1
    with pytest.raises(ValueError,match='Cross-user or cross-card'):verify_neighbors(meta,bad,a,training=True)
    bad=n.copy();bad[2,0]=4
    with pytest.raises(ValueError,match='Future or equal-time'):verify_neighbors(meta,bad,a,training=True)
    meta.loc[0,'event_time']='2018-01-01'
    with pytest.raises(ValueError,match='Development nodes'):verify_neighbors(meta,n,a,training=True)


def test_chronological_reordering_ignores_padding():
    prior=torch.tensor([[[4.],[2.],[99.]],[[88.],[99.],[77.]]])
    mask=torch.tensor([[True,True,False],[False,False,False]])
    sequence,valid,lengths=oldest_first(prior,mask)
    assert sequence.squeeze(-1).tolist()==[[2.,4.,0.],[0.,0.,0.]]
    assert lengths.tolist()==[2,0]
    assert valid.tolist()==[[True,True,False],[False,False,False]]


@pytest.mark.parametrize('mode',['mean','decay','gru','transformer','attention'])
def test_history_cold_start_and_exact_parameter_control(mode):
    torch.set_num_threads(1)
    args=(np.zeros((4,32),dtype=np.float32),24,[4])
    seed_training(42,1);history=Model(Config(mode=mode),*args)
    seed_training(42,1);control=Model(Config(mode=mode,self_control=True),*args)
    assert sum(p.numel() for p in history.parameters())==sum(p.numel() for p in control.parameters())
    assert all(torch.equal(v,control.state_dict()[k]) for k,v in history.state_dict().items())
    numeric=torch.randn(3,24);categories=torch.zeros((3,1),dtype=torch.long);pairs=torch.zeros(3,dtype=torch.long)
    hn=torch.zeros(3,6,24);hc=torch.zeros(3,6,1,dtype=torch.long);hp=torch.zeros(3,6,dtype=torch.long)
    mask=torch.zeros(3,6,dtype=torch.bool);ages=torch.zeros(3,6)
    history.eval();control.eval()
    result=history(numeric,categories,pairs,history=(hn,hc,hp,mask,ages))
    direct=history.point(numeric,categories,pairs)
    assert torch.isfinite(result).all();torch.testing.assert_close(result,direct,atol=0,rtol=0)
    # Self control cannot read actual historical tokens, ages or masks, even if poisoned.
    a=control(numeric,categories,pairs)
    b=control(numeric,categories,pairs,history=(hn+float('nan'),hc,hp,~mask,ages-1))
    torch.testing.assert_close(a,b,atol=0,rtol=0)
    mask[:,:2]=True;ages[:,:2]=torch.tensor([3600.,7200.]);hn[:,:2]=torch.randn(3,2,24)
    history.train();out=history(numeric,categories,pairs,history=(hn,hc,hp,mask,ages))
    assert torch.isfinite(out).all();out.sum().backward()
    assert history.point.core.input[0].weight.grad.abs().sum()>0
    if mode=='gru':assert history.gru.weight_ih_l0.grad.abs().sum()>0
    if mode=='transformer':assert history.transformer.layers[0].self_attn.in_proj_weight.grad.abs().sum()>0


def test_deterministic_history_stats_and_cold_rows():
    frame=pd.DataFrame({'amount_cents':[100.,300.,10000.],'mcc':['5411','5411','5812']})
    n=np.full((3,6),-1,dtype=np.int64);a=np.zeros((3,6),dtype=np.float32)
    n[1,0]=0;a[1,0]=60
    n[2,:2]=[1,0];a[2,:2]=[60,120]
    values=deterministic_statistics(frame,n,a)
    assert values.shape==(3,len(STAT_NAMES))
    assert np.array_equal(values[0],np.zeros(len(STAT_NAMES)))
    assert values[2,STAT_NAMES.index('amount_mean')]==200
    assert values[2,STAT_NAMES.index('amount_std')]==100
    assert values[2,STAT_NAMES.index('same_mcc_fraction')]==0
    assert values[1,STAT_NAMES.index('latest_same_mcc')]==1
    bad=a.copy();bad[0,0]=999
    with pytest.raises(ValueError):deterministic_statistics(frame,n,bad)


def test_null_slots_are_zero_before_model_receives_them():
    tensors=(torch.full((3,24),999.),torch.full((3,1),2,dtype=torch.long),torch.full((3,),2,dtype=torch.long))
    n=np.full((3,6),-1,dtype=np.int64);a=np.zeros((3,6),dtype=np.float32)
    history=gather_history(tensors,n,a,np.array([2]))
    assert all(not v.any() for v in history[:4])


def test_twelve_epoch_history_fit_roundtrip_without_future_context(tmp_path):
    from tests.semantic_detection.test_v21 import frame
    from app.risk.semantic_detection.evaluation import Ranking
    from app.risk.semantic_detection.v2.history_experiment import fit,load
    torch.set_num_threads(1)
    X=frame().iloc[:6].reset_index(drop=True)
    meta=metadata();n,a=historical_neighbors(meta,k=6,group='user_card',training=True)
    y=np.array([0,1,0,1,0,1],dtype=np.int8)
    ranking=Ranking(meta.assign(split='validation',event_time='2018-01-01',channel=X.channel),y)
    keys=[('unknown','unknown'),('5411','unknown'),('5812','unknown')]
    config=Config(mode='gru',threads=1)
    result,scores=fit(config,12,X,y,keys,(n,a),X,np.arange(6),(n,a),ranking,tmp_path/'fit')
    assert len(result['curves'])==12
    assert result['reload_max_abs_diff']<=1e-6
    assert result['training_positive_rows']==3
    assert result['curves'][-1]['learning_rate']<result['curves'][0]['learning_rate']
    assert len(list((tmp_path/'fit').glob('epoch-*.pt')))==12
    replay=load(tmp_path/'fit/model').predict(X,np.arange(6),neighborhood=(n,a))
    np.testing.assert_allclose(replay,scores,atol=1e-6)


def test_gate_B_refuses_missing_gate_A_completion(tmp_path):
    import json
    from app.risk.semantic_detection.v2.history_experiment import run
    gate=tmp_path/'gate.json';gate.write_text(json.dumps({'status':'A1_only'}))
    with pytest.raises(ValueError,match='Completed measured Gate A'):
        run(tmp_path,gate,tmp_path/'absent-policy.json',tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


def test_block_bootstrap_preserves_paired_net_and_known_constant():
    from app.risk.semantic_detection.v2.audit_history import block_interval
    assert block_interval(np.ones(14,dtype=int))['paired_circular_block_95']==[14.,14.]
    a=block_interval(np.array([1,0,-1,2,0,1,0]*3));b=block_interval(np.array([1,0,-1,2,0,1,0]*3))
    assert a==b and a['net']==9 and a['block_days']==7


def test_readonly_gate_probe_cannot_change_outputs():
    from app.risk.semantic_detection.v2.audit_history import GateProbe
    torch.set_num_threads(1);seed_training(42,1)
    model=Model(Config(mode='attention',self_control=True),np.zeros((4,32),dtype=np.float32),24,[4]).eval()
    numeric=torch.randn(4,24);category=torch.zeros(4,1,dtype=torch.long);pairs=torch.zeros(4,dtype=torch.long)
    with torch.inference_mode():expected=model(numeric,category,pairs)
    probe=GateProbe(model,np.array([0,1,0,1]))
    with torch.inference_mode():actual=model(numeric,category,pairs)
    record=probe.finish()
    torch.testing.assert_close(expected,actual,atol=0,rtol=0)
    assert record['0']['rows']==2 and record['1']['rows']==2
    assert 1<=record['1']['attention_effective_neighbors']<=6.000001


def test_broad_statistics_cover_all_inputs_and_mcc_without_labels():
    from app.risk.semantic_detection.v2.expanded_controls import features
    from app.risk.semantic_detection.v2.audit_broad import reconstruct
    numeric=np.array([[1.,2.],[3.,6.],[7.,10.]],dtype=np.float32)
    categories=np.array([1,2,1]);neighbors=np.array([[-1,-1],[0,-1],[1,0]])
    mean,std,latest,freq,count=features(numeric,categories,neighbors,3)
    np.testing.assert_allclose(mean,[[0,0],[1,2],[2,4]])
    np.testing.assert_allclose(std,[[0,0],[0,0],[1,2]])
    np.testing.assert_allclose(latest,[[0,0],[1,2],[3,6]])
    np.testing.assert_allclose(freq,[[0,0,0],[0,1,0],[0,.5,.5]])
    assert count.tolist()==[0,1,2]
    independent=reconstruct(numeric,categories,neighbors,3)
    for actual,expected in zip(independent[:4],(mean,std,latest,freq)):
        np.testing.assert_allclose(actual,expected,atol=1e-7)
    assert independent[4].tolist()==[False,True,True]
