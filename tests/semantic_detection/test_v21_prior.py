from types import SimpleNamespace

import numpy as np
import pytest
import torch

from app.risk.neural_model import seed_training
from app.risk.semantic_detection.v2.matched import Config
from app.risk.semantic_detection.v2.prior_representations import initialized_frozen_model,normalize
from app.risk.semantic_detection.v2.semi_supervised import MaskedBroadModel
from app.risk.semantic_detection.v2.semi_fit import state_fingerprint


def test_frozen_prior_changes_only_table_preserves_RNG_and_matches_capacity():
    torch.set_num_threads(1)
    prep=SimpleNamespace(keys=[('unknown','unknown')]*4,
        numeric=SimpleNamespace(columns=list(range(12)),categorical_sizes=[4]))
    config=Config(representation='learned',include_channel=False,seed=42,threads=1)
    seed_training(42,1);ordinary=MaskedBroadModel(config,prep,220);ordinary_rng=torch.get_rng_state().clone()
    table=normalize(np.random.default_rng(15).normal(size=(4,32)))
    seed_training(42,1);frozen,meta=initialized_frozen_model(config,prep,220,table)
    assert torch.equal(ordinary_rng,torch.get_rng_state())
    assert meta['ordinary_cold_state_sha256']==state_fingerprint(ordinary.state_dict())
    assert meta['trainable_parameters']==meta['total_parameters']-4*32
    assert not frozen.point.aux.weight.requires_grad
    for key,value in ordinary.state_dict().items():
        if key!='point.aux.weight':torch.testing.assert_close(value,frozen.state_dict()[key],atol=0,rtol=0)
    inputs=(torch.randn(8,24),torch.zeros(8,1,dtype=torch.long),torch.tensor([0,1,2,3,1,2,3,1]),torch.randn(8,220))
    frozen.train();frozen(*inputs).sum().backward()
    assert frozen.point.aux.weight.grad is None
    assert frozen.point.aux_input.weight.grad is not None
    torch.testing.assert_close(frozen.point.aux.weight,torch.from_numpy(table),atol=0,rtol=0)


def test_unit_vectors_zero_unknown_and_reject_nonregistered_dimensions():
    values=normalize(np.ones((5,32)))
    assert np.array_equal(values[0],np.zeros(32))
    np.testing.assert_allclose(np.linalg.norm(values[1:],axis=1),1.,rtol=1e-6)
    with pytest.raises(ValueError):normalize(np.ones((5,128)))
    with pytest.raises(ValueError):normalize(np.full((5,32),np.nan))


def test_real_frozen_worker_twelve_epoch_save_reload_without_aux_updates(tmp_path):
    import pandas as pd
    from app.risk.ml_models import MODEL_COLUMNS
    from app.risk.semantic_detection.semantics import UNKNOWN
    from app.risk.semantic_detection.v2.matched import Preprocessor
    from app.risk.semantic_detection.v2.prior_fit import fit
    from app.risk.semantic_detection.v2.semi_experiment import load_student
    frame=pd.DataFrame({c:np.arange(16,dtype=float)%2 for c in MODEL_COLUMNS if c not in ('mcc','channel')})
    frame['mcc']='5411';frame['channel']='Chip Transaction'
    prep=Preprocessor(Config(representation='learned',include_channel=False,threads=2)).fit(frame,[(UNKNOWN,UNKNOWN),('5411',UNKNOWN)])
    table=normalize(np.random.default_rng(17).normal(size=(2,32)))
    stats=np.random.default_rng(19).normal(size=(16,220)).astype(np.float32)
    condition={'name':'self_authored_prior_fixture','loss':'weighted_bce','consistency_weight':0.,'view_probability':0.}
    model,record=fit(frame,stats,np.array([0,1,0,1]+[-1]*12),prep,condition,42,tmp_path/'prior',
        protocol_sha256='FIXTURE',representation_table=table,representation_identity={'kind':'fixture_random'})
    assert record['last_epoch']==12 and len(record['curves'])==12
    assert record['initialization']['trainable_parameters']==record['student_parameters']-64
    torch.testing.assert_close(model.point.aux.weight,torch.from_numpy(table),atol=0,rtol=0)
    restored=load_student(tmp_path/'prior/model.pt',prep);assert not restored.point.aux.weight.requires_grad
    restored.eval();model.eval();inputs=prep.transform(frame)+(torch.from_numpy(stats),)
    with torch.inference_mode():torch.testing.assert_close(restored(*inputs),model(*inputs),atol=0,rtol=0)


def test_prior_gate_rejects_unpublished_or_partial_parent(tmp_path):
    import json
    from pathlib import Path
    from app.risk.semantic_detection.v2.prior_experiment import validate,verify_parent
    policy=json.loads((Path(__file__).resolve().parents[2]/'config/semantic-detection/v21-d-prior-addendum.json').read_text(encoding='utf8'))
    policy.update(implementation_ready=True,registration_commit='a01e131b9d9a5b7ab029e74fcbb69adfea9b3ca6')
    validate(policy)
    policy['seeds']=[42]
    with pytest.raises(ValueError):validate(policy)
    with pytest.raises(ValueError):verify_parent(tmp_path/'uncompleted-parent',tmp_path/'missing-audit')


def test_prior_report_complete_schema_and_pairing(tmp_path,monkeypatch):
    """Self-authored report fixture catches schema errors without real labels."""
    import json
    import pandas as pd
    from app.risk.semantic_detection.common import digest,write_json
    from app.risk.semantic_detection.v2 import prior_report as module
    experiment,audit,parent,output=[tmp_path/n for n in ('experiment','audit','parent','output')]
    names=['ordinary_learned32','qwen_frozen32','random_frozen32','tfidf_frozen32'];scores=np.linspace(.1,.9,16)
    metrics={'tp_daily10':3,'precision':.3,'recall':.75,'ap':.6,
        'channel':{'Online Transaction':{'reviewed':10,'tp':3}}}
    training={'student_parameters':100,'initialization':{'trainable_parameters':68},'seed':42,
        'training_loop_CPU_seconds':1.,'training_loop_wall_seconds':1.,'curves':[{'epoch':1,'supervised_mean_loss':.2}]}
    evaluation={'metrics':metrics,'inference_wall_seconds':1.}
    record={'ensembles':dict.fromkeys(names,metrics),'records':{},'evaluations':{}}
    original={'seed_evaluations':{},'ensembles':{'focal':metrics},'masked_tree':{'metrics':metrics}}
    for name in names:
        directory=parent/'bce' if name=='ordinary_learned32' else experiment/name
        directory.mkdir(parents=True);np.save(directory/'ensemble.npy',scores)
        for seed in (42,43,44):
            key=f'{name}/seed-{seed}'
            if name=='ordinary_learned32':
                (directory/f'seed-{seed}').mkdir()
                write_json(directory/f'seed-{seed}'/'training.json',training);original['seed_evaluations'][f'bce/seed-{seed}']=evaluation
            else:record['records'][key]=training;record['evaluations'][key]=evaluation
    write_json(experiment/'report.json',record);write_json(parent/'report.json',original)
    write_json(experiment/'protocol.json',{'representations':names[1:],'registration_commit':'FIXTURE'})
    pair={'net':0,'added_positive':0,'removed_positive':0,'paired_day_bootstrap_95':[-1,1],
        'block_sensitivity':{'paired_circular_block_95':[-1,1]}}
    audit.mkdir();write_json(audit/'report.json',{'status':'low_label_prior_supplement_independently_verified',
        'full_new_model_replays':9,'report_sha256':digest(experiment/'report.json'),'max_full_replay_abs_diff':0.,
        'comparisons':{f'qwen_vs_{name}':pair for name in ('ordinary_learned32','random_frozen32','tfidf_frozen32')}})
    write_json(tmp_path/'resources.json',{'status':'completed'})
    X=pd.DataFrame({'channel':['Online Transaction']*16});y=np.array([0,1]*8)
    monkeypatch.setattr(module,'load_data',lambda source:(((None,None,None),(X,y,None)),{}))
    result=module.run('SELF_AUTHORED',experiment,audit,parent,output,tmp_path/'resources.json',tmp_path/'resources.json')
    assert result['qwen_vs_ordinary_same_seed_TP_differences']==[0,0,0]
    assert not result['all_prespecified_positive_conditions_met']
    assert (output/'low-label-prior.png').is_file()
    assert '没有新LLM生成' in (output/'LOW_LABEL_PRIOR_RESULTS.md').read_text(encoding='utf8')
