import pytest
from app.evaluation.v3_development import run_b0
from app.evaluation.v3_tasks import build
from app.evaluation.v3_task_audit import audit


def test_development_trajectories_preserve_every_failure_and_do_not_score_heldout(tmp_path):
    dataset=tmp_path/'dataset';build(dataset);audit(dataset,tmp_path/'candidate-audit.json')
    out=tmp_path/'b0';summary=run_b0(dataset,out)
    assert summary['cases']==30 and summary['stage_denominator']==90 and len(list((out/'cases').glob('*.json')))==30
    assert summary['external_model_requests']==summary['evaluation_candidates_scored']==0
    assert not summary['formal_comparison'] and not summary['agent_value_claim']
    with pytest.raises(FileExistsError):run_b0(dataset,out)


def test_evaluation_scoring_cannot_be_started_by_switching_a_flag(tmp_path):
    with pytest.raises(ValueError,match='independent review'):run_b0(tmp_path/'unused',tmp_path/'must-not-exist',fold='evaluation')
    assert not (tmp_path/'must-not-exist').exists()
