"""Same projection and tool permissions; never infer model superiority from fewer outputs."""

import json
from pathlib import Path

import httpx
import pytest

from app.cli.compare_investigation import compare_task, main
from app.llm.cost_ledger import CostLedger
from app.llm.fake_client import FakeLLMClient
from app.llm.offline_investigator import OfflineInvestigationClient
from app.llm.siliconflow_client import MODEL, SiliconFlowClient
from app.risk.fixed_investigation import run_fixed_investigation
from app.risk.task_evaluation import build_development_pack
from app.tools.case_tools import CaseTools


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tasks(tmp_path):
    return build_development_pack(ROOT / "config/investigation-development-v1.json",tmp_path / "pack")


@pytest.mark.parametrize("index",range(9))
def test_fixed_and_scripted_same_projection_permissions_and_gold(tasks,index):
    task = tasks[index]
    arms = compare_task(task,client=OfflineInvestigationClient(),client_mode="SCRIPTED_TEST")
    fixed, agent = arms["fixed"],arms["investigator"]
    assert fixed["projection_sha256"] == agent["projection_sha256"]
    assert fixed["score"]["task_passed"] and agent["score"]["task_passed"]
    assert fixed["score"]["tool_attempts"] == 4 and fixed["score"]["client_requests"] == 0
    assert fixed["score"]["submitted_checks"] == 3
    # Fresh tool registries: attempts begin at one for BOTH arms.
    assert fixed["result"]["tool_trace"][0]["attempt"] == agent["result"]["tool_trace"][0]["attempt"] == 1
    if task["id"] in {"burst","short_coverage"}:
        assert agent["score"]["submitted_checks"] == 1  # different coverage must be reported
        assert agent["score"]["tool_attempts"] < fixed["score"]["tool_attempts"]


def test_investigator_failure_keeps_fixed_result(tasks):
    client = FakeLLMClient([{"role":"assistant","content":'{"findings":[]}'}])
    arms = compare_task(tasks[0],client=client,client_mode="FAILED_FAKE")
    assert arms["fixed"]["score"]["task_passed"]
    assert not arms["investigator"]["score"]["task_passed"]
    assert arms["investigator"]["result"]["report_markdown"] is None


def test_fixed_tool_failure_does_not_publish_report(tasks):
    result = run_fixed_investigation(CaseTools(Path(tasks[0]["manifest"]),max_calls=1))
    assert result["status"] == "tool_error" and result["report_markdown"] is None


def test_same_case_comparison_with_mock_provider_no_gold_in_payload(tasks,tmp_path):
    scripted, payloads = OfflineInvestigationClient(), []
    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        message = scripted.chat(payload["messages"],payload["tools"])
        return httpx.Response(200,json={"model":MODEL,"choices":[{"message":message,"finish_reason":"stop"}],
            "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}})
    policy = json.loads((ROOT / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = SiliconFlowClient("test-placeholder-not-real",policy=policy,ledger=CostLedger(tmp_path / "ledger.sqlite"),run_id="comparison",http_client=http)
        arms = compare_task(tasks[0],client=client,client_mode="HTTP_MOCK_NOT_REAL_LLM")
    assert arms["fixed"]["score"]["task_passed"] and arms["investigator"]["score"]["task_passed"]
    assert len(payloads) == 5
    assert '"gold"' not in json.dumps(payloads) and "normal_seen" not in json.dumps(payloads)


def test_cancelled_investigator_is_scored_and_keeps_evidence(tasks):
    class Cancelled:
        def chat(self,*args,**kwargs): raise KeyboardInterrupt
    arms = compare_task(tasks[0],client=Cancelled(),client_mode="cancel-test")
    assert arms["fixed"]["score"]["task_passed"]
    assert arms["investigator"]["result"]["status"] == "cancelled"
    assert not arms["investigator"]["score"]["task_passed"]


def test_live_cli_requires_explicit_single_task_and_missing_key_stops(monkeypatch,capsys):
    with pytest.raises(SystemExit): main(["--live"])
    monkeypatch.delenv("SILICONFLOW_API_KEY",raising=False)
    monkeypatch.setattr("app.cli.investigate_live.load_saved_key",lambda:None)
    assert main(["--live","--task-id","normal_seen"]) == 2
    assert "未配置密钥" in capsys.readouterr().err
