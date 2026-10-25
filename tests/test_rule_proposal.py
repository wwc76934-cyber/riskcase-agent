"""Evidence-backed templates cannot become arbitrary labels, code or live actions."""

import json
from pathlib import Path

import pytest

from app.llm.fake_client import FakeLLMClient
from app.llm.offline_investigator import OfflineInvestigationClient
from app.risk.investigation import run_investigation
from app.risk.rule_proposal import proposal_context, request_proposal, validate_proposal
from app.risk.task_evaluation import build_development_pack
from app.tools.case_tools import CaseTools


@pytest.fixture
def context(tmp_path):
    spec = Path(__file__).resolve().parents[1] / "config/investigation-development-v1.json"
    tasks = build_development_pack(spec,tmp_path / "pack")
    task = next(task for task in tasks if task["id"]=="large_new")
    investigation = run_investigation(OfflineInvestigationClient(),CaseTools(Path(task["manifest"])),client_mode="test")
    return proposal_context(investigation,amount_threshold=1000)


def proposal(context):
    return {"decision":"propose","template_ids":[item["id"] for item in context["templates"]],
        "evidence_ids":[ref for item in context["templates"] for ref in item["supporting_evidence_ids"]]}


def test_supported_templates_materialize_four_conditions(context):
    rule = validate_proposal(json.dumps(proposal(context)),context)
    assert len(rule["all"])==4
    assert {item["field"] for item in rule["all"]} == {"amount_cents","history_30d_complete","has_history_30d","merchant_seen_30d"}
    assert {item["id"] for item in context["templates"]}=={"large_amount","unseen_merchant"}


@pytest.mark.parametrize("problem",["unknown_template","wrong_reference","missing_reference","duplicate_template","extra_rule","deploy","wrapped_json"])
def test_unsupported_proposals_rejected(context,problem):
    body = proposal(context)
    if problem=="unknown_template": body["template_ids"] = ["is_fraud"]
    if problem=="wrong_reference": body["evidence_ids"] = ["invented"]
    if problem=="missing_reference": body["evidence_ids"] = []
    if problem=="duplicate_template": body["template_ids"] *= 2
    if problem=="extra_rule": body["rule"] = {"field":"is_fraud"}
    if problem=="deploy": body["decision"] = "deploy"
    text = json.dumps(body)
    if problem=="wrapped_json": text = "```json\n"+text+"\n```"
    with pytest.raises(ValueError): validate_proposal(text,context)


def test_abstention_is_allowed_without_fabricated_candidate(context):
    assert validate_proposal('{"decision":"abstain","template_ids":[],"evidence_ids":[]}',context) is None


def test_nonempty_abstention_rejected(context):
    body = proposal(context); body["decision"] = "abstain"
    with pytest.raises(ValueError): validate_proposal(json.dumps(body),context)


def test_four_condition_budget_is_enforced(context):
    context["templates"].append({"id":"test_extra","conditions":[{"field":"count_1h","op":"ge","value":4}],
        "supporting_evidence_ids":context["templates"][0]["supporting_evidence_ids"]})
    with pytest.raises(ValueError,match="four"): validate_proposal(json.dumps(proposal(context)),context)


def test_no_label_metrics_or_paths_in_model_request(context):
    client = FakeLLMClient([{"role":"assistant","content":json.dumps(proposal(context))}])
    result,messages = request_proposal(client,context)
    assert result["status"]=="candidate_validated" and not result["human_approved"] and not result["deployed"]
    text = messages[1]["content"]
    assert '"is_fraud"' not in text and '"metrics"' not in text and '"gold"' not in text


def test_invalid_reply_retained_without_retry_or_replay(context):
    client = FakeLLMClient([{"role":"assistant","content":"bad format"}])
    result,messages = request_proposal(client,context)
    assert result["status"]=="proposal_rejected" and result["rule"] is None
    assert messages[-1]["content"]=="bad format" and len(messages)==3


def test_unvalidated_investigation_cannot_create_context():
    with pytest.raises(ValueError): proposal_context({"status":"report_rejected"},amount_threshold=1000)


@pytest.mark.parametrize("outside",["","Explanation before\n"])
def test_only_whole_response_json_fence_can_be_normalized(context,outside):
    raw = outside+"```json\n"+json.dumps(proposal(context))+"\n```"
    result,messages = request_proposal(FakeLLMClient([{"role":"assistant","content":raw}]),context)
    assert messages[-1]["content"]==raw
    if outside:
        assert result["status"]=="proposal_rejected"
    else:
        assert result["status"]=="candidate_validated"
        assert result["format_normalization"]=="single_json_markdown_fence"


def test_fence_does_not_bypass_evidence_validation(context):
    body = proposal(context); body["evidence_ids"] = ["fabricated"]
    raw = "```json\n"+json.dumps(body)+"\n```"
    result,_ = request_proposal(FakeLLMClient([{"role":"assistant","content":raw}]),context)
    assert result["status"]=="proposal_rejected"
