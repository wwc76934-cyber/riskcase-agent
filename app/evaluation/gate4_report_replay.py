"""Re-submit only archived simulated Qwen reports under an independently approved wire change."""
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import httpx

from app.evaluation.gate4_candidate import (
    CandidateBudget, PROJECT_CAP, UNKNOWN_HOLD, source_fingerprint, summarize_candidate,
)
from app.evaluation.v31_query_development import callback_and_capture
from app.investigation.contracts import digest
from app.investigation.recording import RecordedClient
from app.investigation.v3.delivery import parse, request_for
from app.investigation.v3.gate4_provider import Gate4Policy, Gate4ProviderClient
from app.investigation.v3.output import AcquiredEvidence, OutputFailure
from app.investigation.v3.study_delivery import StudyReviewContract
from app.llm.credentials import load_key
from app.llm.glm_client import LLMError
from app.service.runtime import write_json


def replay_inputs(archive):
    root = Path(archive)
    snapshot, reference = callback_and_capture(callback_available=False)
    old = json.loads((root / "trajectory.json").read_bytes())
    if (json.loads((root / "runtime.json").read_bytes()) != snapshot
            or old["case_id"] != snapshot["case_id"]
            or old["condition"] != "B2"
            or [p["stage"] for p in old["phases"]] != [0, 1, 2]
            or any(p["report"] or p["error"]["code"] != "model_schema" for p in old["phases"])):
        raise ValueError("Expected the archived three simulated Qwen report failures")
    target = snapshot["alert"]["transaction_id"]
    contract = StudyReviewContract(target)
    rows = []
    for phase in old["phases"]:
        stage = phase["stage"]
        folder, = (root / "v3-model-records").glob(f"*-delivery-T{stage}")
        saved = json.loads((folder / "provider-01.json").read_bytes())
        if saved["model"] != "Qwen/Qwen3.8-27B":
            raise ValueError("Unexpected archived extraction model")
        evidence = AcquiredEvidence(case_id=snapshot["case_id"], cutoff=snapshot["cutoff"],
            as_of=snapshot["stage_times"][stage], subject_id=snapshot["subject_id"], target_id=target,
            transaction_ids=[r["transaction_id"] for r in snapshot["transactions"]]).ingest(phase["evidence"])
        extraction = contract.extract(parse(saved["message"]), evidence)
        request = request_for("report", contract.schema_for(extraction, evidence),
            contract.context_for(extraction, evidence), rules=contract.rules_for("report"))
        if request != json.loads((folder / "request-02.json").read_bytes()):
            raise ValueError("Reconstructed request differs from the archived report")
        rows.append((phase, request, extraction, evidence))
    return snapshot, reference, contract, rows


def prepare(archive, baseline):
    snapshot, _, _, rows = replay_inputs(archive)
    policy = Gate4Policy("siliconflow-cn", "Qwen/Qwen3.8-27B", "json_schema", "3", "12",
                         "https://www.siliconflow.cn/pricing", "2026-11-10", expand_hypothesis_branches=True)
    maximum = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens) * len(rows) + UNKNOWN_HOLD
    if Decimal(baseline) + maximum > PROJECT_CAP:
        raise ValueError("Project headroom unavailable")
    return dict(contract="gate4-report-schema-replay-v1", policy=asdict(policy), code_sha256=source_fingerprint(),
                runtime_sha256=digest(snapshot), requests_sha256=digest([row[1] for row in rows]),
                max_requests=len(rows), cap_cny=str(maximum), baseline_cny=str(baseline))


def execute(archive, plan_path, grant_path, ledger_path, output):
    raw = Path(plan_path).read_bytes()
    plan, grant = json.loads(raw), json.loads(Path(grant_path).read_bytes())
    scope = sha256(raw).hexdigest()
    if (plan != prepare(archive, plan["baseline_cny"]) or grant.get("plan_sha256") != scope
            or grant.get("state") != "approved" or not grant.get("user_message")
            or grant.get("cap_cny") != plan["cap_cny"]):
        raise ValueError("New replay grant must bind the unchanged plan")
    output, ledger_path = Path(output), Path(ledger_path)
    key = load_key()
    if output.exists() or not ledger_path.is_file() or not key:
        raise ValueError("Fresh output, existing project ledger and local credential required")
    policy = Gate4Policy(**plan["policy"])
    budget = CandidateBudget(ledger_path, policy=policy, scope=scope, baseline=plan["baseline_cny"],
                             cap=plan["cap_cny"], request_limit=plan["max_requests"])
    snapshot, reference, contract, rows = replay_inputs(archive)
    budget.start()
    output.mkdir()
    results = []
    blocked = False
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as http:
        adapter = Gate4ProviderClient(policy, key, budget=budget, http=http)
        for phase, request, extraction, evidence in rows:
            row = dict(phase, plan=None, report=None, error=None)
            recorder = RecordedClient(adapter, output / f"T{row['stage']}")
            try:
                row["report"] = contract.assemble(parse(recorder.chat(**request)), extraction, evidence)
            except OutputFailure as exc:
                row["error"] = dict(code=exc.code, detail=exc.detail)
            except LLMError as exc:
                row["error"] = dict(code=str(exc))
                blocked = True
            finally:
                write_json(recorder.directory / "audit.json", recorder.snapshot())
            row["process_elapsed_seconds_including_recovery"] = adapter.audit[-1]["elapsed_seconds"] if adapter.audit else 0
            write_json(recorder.directory / "result.json", row)
            results.append(row)
            if blocked:
                break
    result = dict(evidence_kind="separately_authorized_external_client",
                  blocked_uncertain_request=blocked, phases=results)
    summary = summarize_candidate(snapshot, reference, result, budget.summary(budget.prefix))
    summary["scope"] = "observed_report_only_replay"
    write_json(output / "summary.json", summary)
    return summary
