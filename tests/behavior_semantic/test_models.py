import numpy as np
import pytest
import torch
from app.risk.behavior_semantic.baselines import Classical,fit_net,prepare,score_net,BehaviorNet
from app.risk.behavior_semantic.study import static_split,event_key


def fixture(n=40):
    return [{'actions':[[2],[3+i%3],[10+i%2]],'times':[0.,100.,200.],'Amount':10.+i,
        'device_freq':.1,'ip_freq':.2,'beneficiary_freq':.3,'application_freq':.5,'is_fraud':i%4==0} for i in range(n)]


def test_missing_operation_preserves_actual_time_slot():
    row=fixture(1)[0];row['actions']=[[2],[],[10]]
    seq,t=prepare([row],{2:2,10:3})
    assert seq==[[2,1,3]] and t==[[0.,.1,.2]]


def test_frozen_tables_preserve_downstream_initialization():
    torch.manual_seed(42);ordinary=BehaviorNet(20,'gru')
    table=np.random.default_rng(9).normal(size=(20,32)).astype(np.float32);table[0]=0
    torch.manual_seed(42);frozen=BehaviorNet(20,'gru',vectors=table)
    for k,v in ordinary.state_dict().items():
        if k!='embedding.weight':torch.testing.assert_close(v,frozen.state_dict()[k],rtol=0,atol=0)
    assert not frozen.embedding.weight.requires_grad


def test_OS_container_boundary_command(tmp_path):
    from app.risk.behavior_semantic.isolation import conversion_command
    a=tmp_path/'input';a.write_bytes(b'fixture');b=tmp_path/'code.py';b.write_text('pass');d=tmp_path/'output';d.mkdir()
    cmd=conversion_command('python@sha256:'+'a'*64,a,b,d,'fraudnlp-convert-fixture')
    assert cmd[cmd.index('--network')+1]=='none' and '--read-only' in cmd
    assert cmd[cmd.index('--memory')+1]=='2g' and cmd[cmd.index('--user')+1]=='65534:65534'
    assert 'no-new-privileges' in cmd and not any('docker.sock' in s for s in cmd)


@pytest.mark.parametrize('kind',['numeric_lr','numeric_tree','tfidf_lr','tfidf_svm','combined_lr'])
def test_classical_fit_and_unseen_transform(kind):
    rows=fixture();y=[r['is_fraud'] for r in rows];model=Classical(kind).fit(rows,y)
    changed=fixture(8);changed[0]['actions']=[[999]]
    score=model.predict(changed);assert np.isfinite(score).all() and len(score)==8


@pytest.mark.parametrize('kind',['gru','transformer'])
def test_real_neural_fixture_cold_fit_roundtrip(kind):
    rows=fixture();dev=fixture(12)
    model,lookup,prep,record=fit_net(rows,[r['is_fraud'] for r in rows],dev,[r['is_fraud'] for r in dev],kind=kind,max_epochs=2,with_numeric=False)
    seq,times=prepare(dev,lookup);numbers=np.zeros((len(dev),0),np.float32)
    a=score_net(model,seq,times,numbers);restored=BehaviorNet(len(lookup)+2,kind);restored.load_state_dict(model.state_dict())
    np.testing.assert_array_equal(a,score_net(restored,seq,times,numbers))
    assert record['test_seen_by_trainer'] is False and len(record['curves'])==2
