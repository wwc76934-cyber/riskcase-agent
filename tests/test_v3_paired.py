from copy import deepcopy
import pytest
from app.evaluation.v3_paired import paired_family_interval


def fixture():
    plan=dict(conditions=['code','model'],repeats=2,dataset_sha256='dataset',protocol_sha256='protocol',
        cases=[dict(case_id='case-'+str(i),family='family-'+str(i//2)) for i in range(8)])
    rows=[]
    for case in plan['cases']:
        for condition in plan['conditions']:
            for repeat in (1,2):
                passed=condition=='model'
                rows.append(dict(case_id=case['case_id'],condition=condition,repeat=repeat,dataset_sha256='dataset',protocol_sha256='protocol',
                    model_requests=3 if passed else 0,estimated_cny='.01' if passed else '0',risk_hold_cny='0',
                    phases=[dict(stage=s,assessment=dict(delivered=passed,strict_author_reference_pass=passed,full_information_target_correct=passed)) for s in range(3)]))
    return plan,rows


def test_failed_conditions_remain_paired_and_repeats_do_not_inflate_case_count():
    plan,rows=fixture();r=paired_family_interval(plan,rows,'model','code',replicates=1000)
    assert r['point']==1 and r['interval']==[1,1] and r['independent_case_count']==8 and r['family_count']==4
    assert not r['agent_value_claim'] and r['few_family_warning']


def test_missing_runs_and_partial_trajectories_cannot_produce_selected_success_interval():
    plan,rows=fixture();r=paired_family_interval(plan,rows[:-1],'model','code',replicates=1000)
    assert not r['full_sample_complete'] and r['interval'] is None
    rows[-1]['phases'].pop();r=paired_family_interval(plan,rows,'model','code',replicates=1000)
    assert r['incomplete_stage_runs'] and r['point'] is None


def test_duplicate_retry_or_different_frozen_protocol_is_rejected():
    plan,rows=fixture()
    with pytest.raises(ValueError,match='successful retry'):paired_family_interval(plan,rows+[rows[0]],'model','code',replicates=1000)
    rows[0]['protocol_sha256']='another'
    with pytest.raises(ValueError,match='fingerprint'):paired_family_interval(plan,rows,'model','code',replicates=1000)


def test_interval_is_reproducible_and_keeps_cost_risk_holds():
    plan,rows=fixture();rows[-1]['risk_hold_cny']='.50'
    a=paired_family_interval(plan,rows,'model','code',metric='committed_estimate_cny',replicates=1000,seed=7)
    b=paired_family_interval(plan,rows,'model','code',metric='committed_estimate_cny',replicates=1000,seed=7)
    assert a==b and a['point']>.01 and a['interval'][1]>a['interval'][0]
