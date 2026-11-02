"""Preparation only: fixtures verify isolation and gradients, not model efficacy."""
import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import torch

from app.risk.semantic_detection.v2.expanded_controls import BroadStatsModel
from app.risk.semantic_detection.v2.matched import Config
from app.risk.semantic_detection.v2.semi_supervised import (
    CalibratedLoss,MaskedBroadModel,batch_objective,consistency_schedule,
    controlled_visibility,update_teacher)


@pytest.mark.parametrize('name',['weighted_bce','standard_focal','matched_weight_focal','class_balanced_focal'])
def test_loss_scale_matches_initial_visible_expected_loss_only(name):
    labels=np.array([0]*1000+[1]*3+[-1]*7000)
    criterion=CalibratedLoss(name,labels)
    known=torch.tensor(labels[labels>=0],dtype=torch.float64)
    loss=criterion.values(torch.zeros_like(known),known).mean()
    assert float(loss)==pytest.approx(criterion.expected_initial_loss,rel=1e-12)
    assert criterion.metadata()['visible_positive']==3
    assert criterion.metadata()['visible_negative']==1000
    with pytest.raises(ValueError): criterion.values(torch.zeros(1),torch.tensor([-1.]))


@pytest.mark.parametrize('name',['weighted_bce','standard_focal','matched_weight_focal','class_balanced_focal'])
def test_reported_per_class_logit_derivative_matches_autograd(name):
    criterion=CalibratedLoss(name,np.array([0]*101+[1]*3))
    logits=torch.tensor([-30.,-8.,-2.,0.,2.,8.,30.]*2,dtype=torch.float64,requires_grad=True)
    labels=torch.tensor([0.]*7+[1.]*7,dtype=torch.float64)
    criterion.values(logits,labels).sum().backward()
    torch.testing.assert_close(logits.grad.abs(),criterion.logit_gradient_magnitude(logits.detach(),labels),atol=1e-10,rtol=2e-8)
    with pytest.raises(ValueError):criterion.logit_gradient_magnitude(torch.zeros(1),torch.tensor([-1.]))


def training_metadata():
    return pd.DataFrame({'transaction_id':['a','b','c'],'split':['train']*3,
        'event_time':['2017-12-24T23:59:59','2017-12-25T00:00:00','2016-06-01T12:00:00']})


def test_simulated_delay_cutoff_and_hash_mask_do_not_use_hidden_labels():
    meta=training_metadata();y=np.array([1,0,0])
    visible,record=controlled_visibility(meta,y,simulation_acknowledged=True,fraction=1.)
    assert visible.tolist()==[1,-1,0]
    assert not record['real_available_at_verified']
    changed=y.copy();changed[1]=1
    again,second=controlled_visibility(meta,changed,simulation_acknowledged=True,fraction=1.)
    np.testing.assert_array_equal(visible,again);assert record==second
    left,_=controlled_visibility(meta,y,simulation_acknowledged=True,fraction=.5)
    right,_=controlled_visibility(meta,1-y,simulation_acknowledged=True,fraction=.5)
    np.testing.assert_array_equal(left>=0,right>=0)


def test_no_implicit_simulation_or_development_or_unknown_to_normal_conversion():
    meta=training_metadata();y=np.array([1,0,0])
    with pytest.raises(ValueError,match='acknowledgement'):controlled_visibility(meta,y)
    meta.loc[0,'split']='validation'
    with pytest.raises(ValueError,match='training-only'):controlled_visibility(meta,y,simulation_acknowledged=True)
    meta=training_metadata();meta.loc[0,'event_time']='2018-01-01T00:00:00'
    with pytest.raises(ValueError,match='training calendar'):controlled_visibility(meta,y,simulation_acknowledged=True)
    with pytest.raises(ValueError,match='known binary'):controlled_visibility(training_metadata(),[1,2,0],simulation_acknowledged=True)


def test_unknown_rows_have_no_supervised_gradient_and_soft_targets_are_detached():
    criterion=CalibratedLoss('weighted_bce',np.array([0,1,-1,-1]))
    logits=torch.tensor([.4,-.8,.1,1.5],requires_grad=True)
    labels=torch.tensor([1,0,-1,-1]);teacher=torch.full_like(logits,-2.,requires_grad=True)
    loss,_,_=batch_objective(logits,labels,teacher,criterion,0.)
    loss.backward();assert torch.equal(logits.grad[2:],torch.zeros(2));assert teacher.grad is None
    logits.grad=None
    loss,_,_=batch_objective(logits,labels,teacher,criterion,.3)
    loss.backward()
    torch.testing.assert_close(logits.grad[2:],.3*(logits.detach()[2:].sigmoid()-teacher.detach()[2:].sigmoid())/2)
    assert teacher.grad is None


def test_internal_view_preserves_facts_inference_capacity_and_base_dropout_rng():
    torch.set_num_threads(1)
    prep=SimpleNamespace(keys=[('unknown','unknown')]*4,
        numeric=SimpleNamespace(columns=list(range(12)),categorical_sizes=[4]))
    cfg=Config(representation='learned',include_channel=False,threads=1)
    torch.manual_seed(42);base=BroadStatsModel(cfg,prep,220)
    torch.manual_seed(42);model=MaskedBroadModel(cfg,prep,220)
    assert all(torch.equal(value,model.state_dict()[key]) for key,value in base.state_dict().items())
    args=(torch.randn(5,24),torch.zeros(5,1,dtype=torch.long),torch.zeros(5,dtype=torch.long),torch.randn(5,220))
    before=[value.clone() for value in args]
    base.eval();model.eval()
    torch.testing.assert_close(base(*args),model(*args,view_probability=.9),atol=0,rtol=0)
    model.train();torch.manual_seed(11);plain=model(*args);state=torch.get_rng_state().clone()
    torch.manual_seed(11);view=model(*args,view_probability=.1,view_generator=torch.Generator().manual_seed(91))
    assert torch.equal(state,torch.get_rng_state());assert not torch.equal(plain,view)
    for old,new in zip(before,args):torch.testing.assert_close(old,new,atol=0,rtol=0)
    assert sum(p.numel() for p in base.parameters())==sum(p.numel() for p in model.parameters())


def test_teacher_update_is_parameter_averaging_not_target_label_reuse():
    student=torch.nn.Linear(2,1);teacher=copy.deepcopy(student)
    prior=copy.deepcopy(teacher.state_dict())
    with torch.no_grad():
        for value in student.parameters():value.add_(1.)
    update_teacher(teacher,student,.99)
    for key,value in teacher.state_dict().items():torch.testing.assert_close(value,prior[key]+.01)
    assert all(value.grad is None for value in teacher.parameters())


def test_fixed_consistency_warmup_and_ramp():
    assert [consistency_schedule(.5,e) for e in range(1,7)]==pytest.approx([0,0,1/6,1/3,.5,.5])


def test_matched_focal_preserves_BCE_ratio_and_standard_default_is_disclosed_separately():
    loss=CalibratedLoss('matched_weight_focal',np.array([0]*1000+[1]*3))
    assert loss.coefficients[1]/loss.coefficients[0]==pytest.approx(20.)
    derivative=loss.logit_gradient_magnitude(torch.zeros(2),torch.tensor([0.,1.]))
    assert float(derivative[1]/derivative[0])==pytest.approx(20.)
    assert loss.scale==pytest.approx(84.)
    standard=CalibratedLoss('standard_focal',np.array([0]*1000+[1]*3))
    assert standard.coefficients[1]/standard.coefficients[0]==pytest.approx(1/3)


def test_actual_masked_worker_roundtrip_and_bounded_own_checkpoint_diagnosis(tmp_path):
    from app.risk.ml_models import MODEL_COLUMNS
    from app.risk.semantic_detection.semantics import UNKNOWN
    from app.risk.semantic_detection.v2.matched import Preprocessor
    from app.risk.semantic_detection.v2.semi_fit import fit,state_fingerprint
    frame=pd.DataFrame({c:np.arange(16,dtype=float)%2 for c in MODEL_COLUMNS if c not in ('mcc','channel')})
    frame['mcc']='5411';frame['channel']='Chip Transaction'
    prep=Preprocessor(Config(representation='learned',include_channel=False,threads=2)).fit(frame,[(UNKNOWN,UNKNOWN),('5411',UNKNOWN)])
    statistics=np.random.default_rng(8).normal(size=(16,220)).astype(np.float32)
    visible=np.array([0,1,0,1]+[-1]*12)
    condition={'name':'fixture_only','loss':'weighted_bce','consistency_weight':0.,'view_probability':0.}
    model,record=fit(frame,statistics,visible,prep,condition,42,tmp_path/'main',protocol_sha256='fixture')
    assert len(record['curves'])==12 and record['last_epoch']==12
    assert record['teacher_trainable_parameters']==0
    assert all(row['visible_positive_rows']==2 and row['visible_negative_rows']==2 and row['unlabeled_rows']==12 for row in record['curves'])
    checkpoint=torch.load(tmp_path/'main/model.pt',weights_only=True)
    restored=MaskedBroadModel(Config(**checkpoint['config']),prep,220);restored.load_state_dict(checkpoint['state_dict'])
    assert state_fingerprint(model.state_dict())==state_fingerprint(restored.state_dict())
    assert record['curves'][-1]['learning_rate']==pytest.approx(.00005+(.001-.00005)*(1+np.cos(np.pi*11/12))/2)
    _,diagnostic=fit(frame,statistics,visible,prep,condition,42,tmp_path/'diagnostic',protocol_sha256='fixture',resume_checkpoint=tmp_path/'main/epoch-12.pt')
    assert [row['epoch'] for row in diagnostic['curves']]==list(range(13,19))
    assert all(row['learning_rate']==pytest.approx(.00005) for row in diagnostic['curves'])
    assert record['model_sha256']==__import__('hashlib').sha256((tmp_path/'main/model.pt').read_bytes()).hexdigest()
    poisoned=visible.copy();poisoned[0]=256
    with pytest.raises(ValueError,match='aligned arrays'):
        fit(frame,statistics,poisoned,prep,condition,42,tmp_path/'invalid',protocol_sha256='fixture')
