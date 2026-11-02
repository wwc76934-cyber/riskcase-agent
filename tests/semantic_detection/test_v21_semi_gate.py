"""Admission must fail before real data, and bind all independently checked bytes."""
import hashlib
import json

import pytest

from app.risk.semantic_detection.v2 import semi_experiment as experiment


def write(path,value):
    path.write_text(json.dumps(value),encoding='utf8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ready_policy():
    return {'status':'frozen_after_B_verified','execution_allowed':True,'conditions':experiment.conditions(),'seeds':[42,43,44],
        'main_epochs':12,'diagnostic_conditions':['bce','focal','focal_matched','cbfocal'],'diagnostic_seed':42,'diagnostic_extra_epochs':6,
        'source_archive_sha256':'e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2',
        'simulation_acknowledged':True,'evaluation_scope':'previously_observed_2018_development',
        'source_usage_scope':'existing_private_synthetic_research_no_raw_redistribution',
        'pretrained_semantic_branch_admitted':False,
        'main_neural_instances':27,'diagnostic_continuations':4,'masked_tree_instances':1,'expected_full_replays':32,
        'visibility':{'fraction':.1,'seed':20261102,'delay_days':7,'cutoff':'2017-12-31T23:59:59'}}


def test_conditional_policy_and_incomplete_B_stop_before_loading_or_masking(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Real dataset must not be opened before gate')
    monkeypatch.setattr(experiment,'load_data',forbidden)
    policy=ready_policy();policy['status']='conditional_draft';write(tmp_path/'policy.json',policy)
    with pytest.raises(ValueError,match='draft'):
        experiment.run('unused','unused','unused',tmp_path/'B.json',tmp_path/'review.json',tmp_path/'policy.json',tmp_path/'output')
    policy['status']='frozen_after_B_verified';write(tmp_path/'policy.json',policy)
    with pytest.raises(ValueError,match='Gate B'):
        experiment.run('unused','unused','unused',tmp_path/'B.json',tmp_path/'review.json',tmp_path/'policy.json',tmp_path/'output')
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('field,value',[
    ('conditions',[{'name':'only_an_advantageous_subset'}]),('seeds',[42]),('main_epochs',2),
    ('diagnostic_extra_epochs',60),('simulation_acknowledged',False),
    ('evaluation_scope','new_independent_test'),('pretrained_semantic_branch_admitted',True),
    ('source_archive_sha256','unverified_new_data')])
def test_no_grid_budget_source_or_scope_shortcuts(field,value):
    policy=ready_policy();policy[field]=value
    with pytest.raises(ValueError):experiment.validate_policy(policy)


def test_completed_B_and_reuse_are_bound_to_exact_bytes(tmp_path):
    sources={};artifacts={}
    for i in range(5):
        path=tmp_path/f'B-source-{i}.json';sources[str(path)]=write(path,{'fixture_only':i})
    for i in range(4):
        path=tmp_path/f'label-free-fixture-{i}.json';artifacts[str(path)]=write(path,{'fixture_only':i})
    report={'status':'Gate_B_complete_and_independently_verified','full_model_replays':43,
        'default_detector_changed':False,'final_2019_read':False,
        'tests':{'tests':10,'failures':0,'errors':0},'source_reports':sources}
    report_path=tmp_path/'report.json';report_sha=write(report_path,report)
    review_path=tmp_path/'review.json';write(review_path,{'status':'Gate_B_technical_and_visual_review_complete',
        'history_results_sha256':report_sha,'reuse_artifacts_sha256':artifacts})
    assert experiment.verify_B(report_path,review_path)['history_results_sha256']==report_sha
    (tmp_path/'label-free-fixture-2.json').write_text('changed',encoding='utf8')
    with pytest.raises(ValueError,match='artifact changed'):experiment.verify_B(report_path,review_path)
