"""Failure-boundary tests for persistent execution, not fraud-model accuracy."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.risk.merchant_research import MerchantQuestion, ScriptedMerchantClient
from app.service.api import create_app
from app.service.runtime import ResearchService, Settings, Worker
from app.service.store import Blocked, Cancelled, Conflict, JobStore, LeaseLost, digest
from tests.test_merchant_research import manifest  # Shared synthetic source fixture; no downloaded data/key.


@pytest.fixture
def service(tmp_path, manifest):
    return ResearchService(Settings(Path(__file__).resolve().parents[1], tmp_path / "service", {"fixture": manifest}, "t"*40))


def submit(service, key="request-0001", **kwargs):
    return service.submit(key, case_id="fixture", question={"min_records": 1}, **kwargs)[0]


@pytest.fixture
def api(service):
    with TestClient(create_app(service), headers={"Authorization": "Bearer " + service.settings.token}) as client:
        yield client


def test_api_full_report_and_version_bound_review(service, api):
    response = api.post("/jobs", json={"case_id": "fixture", "question": {"min_records": 1}}, headers={"Idempotency-Key": "request-0001"})
    assert response.status_code == 202
    identity = response.json()["id"]
    again = api.post("/jobs", json={"case_id": "fixture", "question": {"min_records": 1}}, headers={"Idempotency-Key": "request-0001"})
    assert again.status_code == 200 and again.json()["id"] == identity
    row = Worker(service).step()
    assert row["state"] == "awaiting_review" and row["result"]["new_client_requests"] == 4
    assert "用户内商户分布" in api.get(f"/jobs/{identity}/artifacts/report.md").text
    body = {"expected_version": row["version"], "result_hash": row["result_hash"], "decision": "accept_report"}
    assert api.post(f"/jobs/{identity}/review", json=body, headers={"Idempotency-Key": "review-0001"}).json() == {
        "state": "completed", "version": 4, "policy_approved": False, "deployed": False}
    assert api.post(f"/jobs/{identity}/review", json=body, headers={"Idempotency-Key": "review-0001"}).status_code == 200
    assert api.post(f"/jobs/{identity}/review", json=body, headers={"Idempotency-Key": "review-0002"}).status_code == 409
    assert len(api.get(f"/jobs/{identity}/events").json()["events"]) >= 12


def test_unauthenticated_and_host_boundary(service, api):
    assert api.get("/health", headers={"Authorization": "wrong"}).status_code == 401
    assert api.get("/health", headers={"Host": "evil.invalid"}).status_code == 400
    assert api.get("/health").json()["live_enabled"] is False
    assert api.get("/cases").json() == {"case_ids": ["fixture"]}


@pytest.mark.parametrize("body", [
    {"case_id": "../../secret"}, {"case_id": "fixture", "path": "secret"},
    {"case_id": "fixture", "question": {"min_records": True}},
    {"case_id": "fixture", "question": {"min_records": 0}},
    {"case_id": "fixture", "question": {"short_hours": 168, "long_hours": 168}},
    {"case_id": "fixture", "question": {"long_hours": 24}},
    {"case_id": "missing"}, {"case_id": "fixture", "mode": "live"},
    {"case_id": "fixture", "api_key": "SECRET-MUST-NOT-ECHO"},
])
def test_api_rejects_untrusted_inputs(api, body):
    response = api.post("/jobs", json=body, headers={"Idempotency-Key": "request-0001"})
    assert response.status_code == 422
    assert "SECRET-MUST-NOT-ECHO" not in response.text


@pytest.mark.parametrize("body,code", [('{"case_id":"fixture","case_id":"fixture"}', 400),
                                      ('{"case_id":"fixture","question":{"min_records":NaN}}', 400),
                                      ('x'*8193, 413), ('{', 400)])
def test_strict_json_and_body_limit(api, body, code):
    assert api.post("/jobs", content=body, headers={"Content-Type": "application/json", "Idempotency-Key": "request-0001"}).status_code == code


def test_idempotency_payload_change_and_missing_key(api):
    assert api.post("/jobs", json={"case_id": "fixture"}).status_code == 422
    headers = {"Idempotency-Key": "request-0001"}
    assert api.post("/jobs", json={"case_id": "fixture"}, headers=headers).status_code == 202
    assert api.post("/jobs", json={"case_id": "fixture", "question": {"min_records": 1}}, headers=headers).status_code == 409


def test_cancel_queued_never_executes(service, api):
    identity = submit(service)
    assert api.post(f"/jobs/{identity}/cancel").json()["state"] == "cancelled"
    assert Worker(service).step() is None
    assert api.get(f"/jobs/{identity}/artifacts/report.md").status_code == 409
    assert api.get(f"/jobs/{identity}/artifacts/auth.token").status_code == 404
    assert api.get("/jobs/missing").status_code == 404


def test_cancel_during_call_saves_response_but_no_report(service):
    identity = submit(service)
    class Cancelling(ScriptedMerchantClient):
        def chat(self, messages, tools=None):
            service.store.cancel(identity)
            return super().chat(messages, tools)
    row = Worker(service, client_factory=Cancelling).step()
    assert row["state"] == "cancelled" and row["result"] is None
    with sqlite3.connect(service.store.path) as db:
        assert db.execute("SELECT state FROM calls").fetchone()[0] == "completed"


def test_racing_claims_have_only_one_owner(service):
    identity = submit(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda owner: service.store.claim(owner), ("a", "b")))
    assert sum(row is not None for row in claims) == 1
    assert next(row for row in claims if row)["id"] == identity


def test_racing_submissions_are_one_job(service):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(service), range(2)))
    assert results[0] == results[1]


def test_restart_replays_durable_response_without_recalling_provider(service):
    identity = submit(service)
    now = [1000.]
    service.store.clock = lambda: now[0]
    class Crash(BaseException): pass
    class Counting(ScriptedMerchantClient):
        total = 0
        def chat(self, *args, **kwargs):
            type(self).total += 1
            return super().chat(*args, **kwargs)
    def crash_after_saved(n):
        if n == 2: raise Crash()
    with pytest.raises(Crash): Worker(service, client_factory=Counting, after_saved=crash_after_saved).step()
    assert Counting.total == 2
    assert Worker(service).step() is None  # An unexpired lease cannot be stolen.
    now[0] += 91
    row = Worker(service, client_factory=Counting).step()
    assert row["state"] == "awaiting_review" and row["generation"] == 2
    assert row["result"]["replayed_client_responses"] == 2
    assert row["result"]["new_client_requests"] == 2 and Counting.total == 4
    assert service.store.get(identity)["deadline"] == 1180  # Not reset by restart.


def test_ambiguous_request_blocks_instead_of_retrying(service):
    identity = submit(service)
    class Timeout(ScriptedMerchantClient):
        def chat(self, *args, **kwargs): raise TimeoutError("do not leak provider secret")
    row = Worker(service, client_factory=Timeout).step()
    assert row["state"] == "blocked" and row["reason"] == "unresolved_call"
    assert "secret" not in json.dumps(service.store.events(identity))
    assert Worker(service).step() is None


def test_crash_after_intent_blocks_on_lease_expiry(service):
    identity = submit(service)
    now = [1000.]; service.store.clock = lambda: now[0]
    claim = service.store.claim("dead")
    service.store.begin_call(identity, "dead", claim["generation"], 1, digest("request"))
    now[0] += 91
    assert service.store.claim("new") is None
    assert service.store.get(identity)["reason"] == "unresolved_call"


def test_old_worker_cannot_publish_or_save_after_lease_lost(service):
    identity = submit(service)
    now = [1000.]; service.store.clock = lambda: now[0]
    first = service.store.claim("old")
    now[0] += 91
    second = service.store.claim("new")
    assert second["generation"] == 2
    with pytest.raises(LeaseLost): service.store.finish(identity, "old", first["generation"], "awaiting_review", result={})
    with pytest.raises(LeaseLost): service.store.complete_call(identity, "old", first["generation"], 1, {})


def test_deadline_survives_restarts(service):
    identity = submit(service)
    now = [1000.]; service.store.clock = lambda: now[0]
    service.store.claim("a")
    now[0] += 181
    assert service.store.claim("b") is None
    assert service.store.get(identity)["state"] == "blocked"


def test_journal_changed_request_or_response_cannot_replay(service):
    identity = submit(service)
    claim = service.store.claim("a")
    args = (identity, "a", claim["generation"], 1)
    assert service.store.begin_call(*args, digest("one")) is None
    service.store.complete_call(*args, {"role": "assistant", "content": "one"})
    with pytest.raises(Blocked, match="replay_request_changed"): service.store.begin_call(*args, digest("two"))
    with sqlite3.connect(service.store.path) as db:
        db.execute("UPDATE calls SET response=?", ('{"content":"tampered"}',))
    with pytest.raises(Blocked, match="journal_hash_mismatch"): service.store.begin_call(*args, digest("one"))


def test_immutable_input_tampering_blocks_execution(service):
    identity = submit(service)
    row = service.store.get(identity)
    (service.settings.directory / "inputs" / row["spec"]["input_key"] / "manifest.json").write_text("{}")
    assert Worker(service).step()["reason"] == "case_input_changed"


def test_runtime_change_blocks_execution(service, monkeypatch):
    submit(service)
    monkeypatch.setattr("app.service.runtime.fingerprint", lambda _: "changed")
    assert Worker(service).step()["reason"] == "runtime_changed"


def test_report_tampering_cannot_be_downloaded_or_reviewed(service, api):
    identity = submit(service)
    row = Worker(service).step()
    path = service.artifact(identity, "report.md")
    path.write_text("tampered", encoding="utf-8")
    assert api.get(f"/jobs/{identity}/artifacts/report.md").status_code == 409
    assert api.post(f"/jobs/{identity}/review", json={"expected_version": row["version"], "result_hash": row["result_hash"], "decision": "accept_report"}, headers={"Idempotency-Key": "review-0001"}).status_code == 409


def test_http_mock_cost_reservations_survive_worker_restart(service):
    import httpx
    from app.llm.cost_ledger import BudgetExceeded, CostLedger
    from app.llm.siliconflow_client import SiliconFlowClient
    identity = submit(service)
    ledger = CostLedger(service.settings.directory / "mock-budget.sqlite")
    policy = json.loads((service.settings.repo / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    now = [1000.]; service.store.clock = lambda: now[0]
    external = []
    scripted = ScriptedMerchantClient(MerchantQuestion(min_records=1))
    def handler(request):
        external.append(1)
        payload = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": scripted.chat(payload["messages"], payload["tools"])}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 100, "total_tokens": 2100}})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    def factory(question):
        return SiliconFlowClient("MOCK-NOT-A-REAL-KEY", policy=policy, ledger=ledger, run_id=identity, http_client=http)
    class Crash(BaseException): pass
    def crash(n):
        if n == 2: raise Crash()
    try:
        with pytest.raises(Crash): Worker(service, client_factory=factory, after_saved=crash).step()
        assert len(ledger.summary(identity)["entries"]) == 2
        now[0] += 91
        row = Worker(service, client_factory=factory).step()
        assert row["state"] == "awaiting_review"
        assert len(external) == len(ledger.summary(identity)["entries"]) == 4
        # Same persistent run ID retains the six-request ceiling across processes.
        for _ in range(2): ledger.reserve(identity, ".01", run_budget="1", project_budget="20", max_requests=6)
        with pytest.raises(BudgetExceeded, match="request_limit"):
            ledger.reserve(identity, ".01", run_budget="1", project_budget="20", max_requests=6)
    finally: http.close()


def test_received_response_cannot_publish_if_cancel_wins_finish(service):
    identity = submit(service)
    claim = service.store.claim("owner")
    service.store.cancel(identity)
    service.store.finish(identity, "owner", claim["generation"], "awaiting_review", result={"unsafe": True})
    row = service.store.get(identity)
    assert row["state"] == "cancelled" and row["result"] is None


def test_live_http_timeout_uses_original_remaining_deadline(service, monkeypatch):
    """Exercise the live construction path using HTTP Mock only, never saved keys/network."""
    from dataclasses import replace
    import httpx
    from app.llm.cost_ledger import CostLedger
    from app.llm.siliconflow_client import SiliconFlowClient
    service.settings = replace(service.settings, allow_live=True)
    identity = submit(service, mode="live")
    now = [1000.]; service.store.clock = lambda: now[0]
    ledger = CostLedger(service.settings.directory / "mock-budget.sqlite")
    monkeypatch.setattr("app.cli.investigate_live.local_key", lambda: "MOCK-NOT-A-REAL-KEY")
    monkeypatch.setattr("app.llm.cost_ledger.CostLedger", lambda _: ledger)
    timeouts = []
    scripted = ScriptedMerchantClient(MerchantQuestion(min_records=1))
    def handler(request):
        timeouts.append(request.extensions["timeout"]["read"])
        body = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": scripted.chat(body["messages"], body["tools"])}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 100, "total_tokens": 2100}})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.llm.siliconflow_client.SiliconFlowClient", lambda key, **kwargs: SiliconFlowClient(key, **kwargs, http_client=http))
    class Crash(BaseException): pass
    def crash(n):
        if n == 2: raise Crash()
    try:
        with pytest.raises(Crash): Worker(service, after_saved=crash).step()
        now[0] += 171  # Nine seconds remain, not another 180 seconds.
        row = Worker(service).step()
        assert row["state"] == "awaiting_review" and row["deadline"] == 1180
        assert timeouts[:2] == [45, 45]
        assert all(0 < value <= 9 for value in timeouts[2:]) and len(timeouts) == 4
        assert len(ledger.summary(identity)["entries"]) == 4
    finally: http.close()
