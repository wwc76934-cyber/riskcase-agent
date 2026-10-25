import json
from pathlib import Path

import pytest

from app.risk.merchant_evaluation import build_merchant_pack, score_merchant
from app.risk.merchant_research import MerchantQuestion, MerchantResearchTools, ScriptedMerchantClient, run_merchant_research


SPEC = Path(__file__).resolve().parents[1] / "config/merchant-development-v1.json"


@pytest.fixture(scope="module")
def tasks(tmp_path_factory):
    return build_merchant_pack(SPEC,tmp_path_factory.mktemp("merchant-dev") / "pack")


@pytest.mark.parametrize("identity",["short_higher","short_lower","equal","opposite_directions","too_few_short_records","incomplete_month","no_positive_amounts","empty_history"])
def test_authored_gold_end_to_end(tasks,identity):
    task = next(task for task in tasks if task["id"]==identity)
    question = MerchantQuestion(**task["question"])
    tools = MerchantResearchTools(Path(task["manifest"]))
    result = run_merchant_research(ScriptedMerchantClient(question),tools,question,client_mode="SCRIPTED_NOT_LLM")
    assert score_merchant(result,task["gold"])["task_passed"]
    assert result["tool_attempts"]==3
    assert all(row["transaction"]["user_id"]=="fixture-user" for row in result["evidence"].values() if row["tool"]=="get_alert_transaction")
    assert all(row["merchant_id"] not in {"future-excluded","same-time-excluded","other-excluded"} for row in tools.prior)
    assert "gold" not in json.dumps(result["messages"])


@pytest.mark.parametrize("mode,expected",[("prose","answer_format"),("wrong_direction","answer_evidence_or_contract")])
def test_failure_stage_and_no_report(tasks,mode,expected):
    question = MerchantQuestion()
    class BadFinalClient(ScriptedMerchantClient):
        def chat(self,messages,tools=None):
            response = super().chat(messages,tools)
            if not response.get("tool_calls"):
                if mode=="prose": response["content"]="My answer: "+response["content"]
                else:
                    body=json.loads(response["content"])
                    body["count_top1"]="decreased"
                    response["content"]=json.dumps(body)
            return response
    result = run_merchant_research(BadFinalClient(question),MerchantResearchTools(Path(tasks[0]["manifest"])),question,client_mode="BAD_FINAL_TEST")
    assert result["status"]=="answer_rejected" and result["failure_stage"]==expected
    assert result["report_markdown"] is None and result["client_requests"]==4


def test_failed_result_stays_in_score_denominator():
    result = score_merchant({"status":"answer_rejected","findings":None},{"count_top1":"unchanged","positive_amount_top1":"unchanged"})
    assert not result["task_passed"] and result["required_metrics"]==2 and result["correct_metrics"]==0


@pytest.mark.parametrize("mutation",["heldout","duplicate_id","path_id"])
def test_pack_provenance_and_ids_checked_before_creation(tmp_path,mutation):
    spec = json.loads(SPEC.read_text())
    if mutation=="heldout": spec["split"]="heldout"
    if mutation=="duplicate_id": spec["cases"][1]["id"]=spec["cases"][0]["id"]
    if mutation=="path_id": spec["cases"][0]["id"]="../outside"
    source = tmp_path / "spec.json"
    source.write_text(json.dumps(spec),encoding="utf-8")
    with pytest.raises(ValueError): build_merchant_pack(source,tmp_path / "output")
    assert not (tmp_path / "output").exists()


class PresentationClient(ScriptedMerchantClient):
    """One bad presentation followed by a configurable correction, no network."""
    def __init__(self,question,*,repair_mode="valid",first_mode="prose"):
        super().__init__(question)
        self.repair_mode,self.first_mode=repair_mode,first_mode
        self.requests=[]
        self.first_final=None

    def chat(self,messages,tools=None):
        from copy import deepcopy
        from app.llm.glm_client import LLMError
        self.requests.append({"messages":deepcopy(messages),"tools":deepcopy(tools)})
        if self.first_final is not None:
            if self.repair_mode=="error": raise LLMError("simulated provider failure")
            if self.repair_mode=="cancel": raise KeyboardInterrupt()
            if self.repair_mode=="tools":
                from app.llm.fake_client import make_tool_call_message
                return make_tool_call_message("forbidden","get_alert_transaction",{})
            response=deepcopy(self.first_final)
            if self.repair_mode=="prose": response["content"]="Answer: "+response["content"]
            if self.repair_mode=="wrong":
                body=json.loads(response["content"])
                body["count_top1"]="increased"
                response["content"]=json.dumps(body)
            return response
        response=super().chat(messages,tools)
        if not response.get("tool_calls"):
            self.first_final=deepcopy(response)
            if self.first_mode=="prose": response["content"]="Answer: "+response["content"]
            if self.first_mode=="wrong":
                body=json.loads(response["content"])
                body["count_top1"]="decreased"
                response["content"]=json.dumps(body)
        return response


def test_one_format_correction_uses_existing_evidence_and_total_budget(tasks):
    question=MerchantQuestion()
    client=PresentationClient(question)
    tools=MerchantResearchTools(Path(tasks[0]["manifest"]))
    result=run_merchant_research(client,tools,question,client_mode="MOCK",allow_format_repair=True)
    assert result["status"]=="completed_validated"
    assert result["first_attempt"]["status"]=="answer_rejected"
    assert result["format_repair"]["attempted"] and result["format_repair"]["status"]=="validated"
    assert result["client_requests"]==5 and result["tool_attempts"]==3
    assert client.requests[-1]["tools"]==[]
    assert "gold" not in client.requests[-1]["messages"][-1]["content"]
    assert result["messages"][-2]["role"]=="user" and result["messages"][-1]["role"]=="assistant"


@pytest.mark.parametrize("mode,status,stage",[("prose","answer_rejected","answer_format"),("wrong","answer_rejected","answer_evidence_or_contract"),
    ("tools","model_or_protocol_error",None),("error","model_or_protocol_error",None),("cancel","cancelled",None)])
def test_correction_does_not_bypass_validation_or_execute_tools(tasks,mode,status,stage):
    question=MerchantQuestion()
    task=next(task for task in tasks if task["id"]=="incomplete_month")
    result=run_merchant_research(PresentationClient(question,repair_mode=mode),MerchantResearchTools(Path(task["manifest"])),question,client_mode="MOCK",allow_format_repair=True)
    assert result["status"]==status and result["failure_stage"]==stage
    assert result["findings"] is None and result["report_markdown"] is None
    assert result["client_requests"]==5 and result["tool_attempts"]==3


def test_valid_json_with_wrong_conclusion_never_gets_a_correction(tasks):
    question=MerchantQuestion()
    result=run_merchant_research(PresentationClient(question,first_mode="wrong"),MerchantResearchTools(Path(tasks[0]["manifest"])),question,client_mode="MOCK",allow_format_repair=True)
    assert result["status"]=="answer_rejected" and result["failure_stage"]=="answer_evidence_or_contract"
    assert result["client_requests"]==4 and not result["format_repair"]["attempted"]


def test_correction_cannot_reset_total_request_budget(tasks):
    question=MerchantQuestion()
    result=run_merchant_research(PresentationClient(question),MerchantResearchTools(Path(tasks[0]["manifest"])),question,client_mode="MOCK",allow_format_repair=True,max_requests=4)
    assert result["status"]=="answer_rejected" and result["client_requests"]==4
    assert result["format_repair"]["status"]=="request_budget_exhausted" and not result["format_repair"]["attempted"]


@pytest.mark.parametrize("kwargs",[{"max_requests":7},{"max_requests":True},{"allow_format_repair":1}])
def test_explicit_repair_policy_parameters(tasks,kwargs):
    with pytest.raises(ValueError):
        run_merchant_research(ScriptedMerchantClient(MerchantQuestion()),MerchantResearchTools(Path(tasks[0]["manifest"])),MerchantQuestion(),client_mode="MOCK",**kwargs)


def test_first_and_final_scores_stay_separate_and_auditable(tasks):
    from copy import deepcopy
    from scripts.verify_merchant_research import verify_answer_attempts
    question=MerchantQuestion()
    result=run_merchant_research(PresentationClient(question),MerchantResearchTools(Path(tasks[0]["manifest"])),question,client_mode="MOCK",allow_format_repair=True)
    score=score_merchant(result,tasks[0]["gold"])
    assert score["task_passed"] and score["first_attempt_passed"] is False and score["format_repair_attempted"]
    verify_answer_attempts(result,result,question)
    altered=deepcopy(result)
    altered["first_attempt"]={"status":"completed_validated","failure_stage":None,"findings":result["findings"]}
    with pytest.raises(ValueError): verify_answer_attempts(altered,altered,question)


def test_successful_first_answer_does_not_trigger_repair(tasks):
    question=MerchantQuestion()
    result=run_merchant_research(ScriptedMerchantClient(question),MerchantResearchTools(Path(tasks[0]["manifest"])),question,client_mode="MOCK",allow_format_repair=True)
    assert result["status"]=="completed_validated" and not result["format_repair"]["attempted"]
    assert score_merchant(result,tasks[0]["gold"])["first_attempt_passed"]
