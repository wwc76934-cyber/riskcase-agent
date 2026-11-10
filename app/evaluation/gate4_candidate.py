"""One development trajectory per approved Gate 4 candidate."""
import asyncio
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import httpx

from app.evaluation.v31_query_development import callback_and_capture
from app.evaluation.v31_query_score import assess_facts, state_check
from app.investigation.contracts import digest
from app.investigation.v3.gate4_provider import Gate4Policy, Gate4ProviderClient
from app.investigation.v3.mcp_trajectory import run_case_mcp
from app.investigation.v3.output import AcquiredEvidence
from app.investigation.v3.study_delivery import StudyHybridReviewContract, StudyReviewContract
from app.llm.cost_ledger import BudgetExceeded, CostLedger
from app.llm.credentials import load_key
from app.service.runtime import write_json
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess


PROJECT_CAP = Decimal("34")
UNKNOWN_HOLD = Decimal(".50")
MAX_REQUESTS = 12
MAX_COMPATIBILITY_REQUESTS = 15
MAX_FORMAL_CASE_REQUESTS = 18
ROOT = Path(__file__).resolve().parents[2]


class CandidateBudget(CostLedger):
    def __init__(self, path, *, policy, scope, baseline, cap, request_limit=MAX_REQUESTS, project_cap=PROJECT_CAP):
        self.policy_binding = policy.binding
        self.prefix = "g4-smoke:" + scope + ":"
        self.baseline, self.cap = Decimal(baseline), Decimal(cap)
        self.request_limit = request_limit
        self.project_cap = Decimal(project_cap)
        self.bound = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens)
        if (type(request_limit) is not int or not 1 <= request_limit <= MAX_FORMAL_CASE_REQUESTS
                or not self.baseline.is_finite() or self.baseline < 0
                or not self.project_cap.is_finite() or self.project_cap < PROJECT_CAP
                or self.cap != self.bound * request_limit + UNKNOWN_HOLD):
            raise ValueError("Candidate budget must match frozen bounds")
        super().__init__(path)

    def start(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS g4_candidate_started(scope TEXT PRIMARY KEY)")
            if db.execute("SELECT 1 FROM g4_candidate_started WHERE scope=?", (self.prefix,)).fetchone():
                raise ValueError("Approved candidate already started")
            rows = db.execute("SELECT reserved_cny,estimated_cny FROM reservations").fetchall()
            committed = self.committed(rows) + self.holds(db)
            if committed < self.baseline or committed + self.cap > self.project_cap:
                raise BudgetExceeded("candidate_project_headroom")
            db.execute("INSERT INTO g4_candidate_started VALUES (?)", (self.prefix,))

    def reserve(self, amount):
        if not 0 < amount <= self.bound:
            raise BudgetExceeded("candidate_request_bound")
        identity = self.prefix + uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,reserved_cny,estimated_cny,status FROM reservations").fetchall()
            own = [r for r in rows if r[0].startswith(self.prefix)]
            if len(own) >= self.request_limit or any(r[3] != "estimated_from_usage" for r in own):
                raise BudgetExceeded("candidate_exhausted_or_uncertain")
            committed = self.committed([r[1:3] for r in rows]) + self.holds(db)
            prior = committed - self.committed([r[1:3] for r in own])
            if (prior < self.baseline or committed + UNKNOWN_HOLD > self.project_cap
                    or self.committed([r[1:3] for r in own]) + UNKNOWN_HOLD > self.cap):
                raise BudgetExceeded("candidate_budget_limit")
            db.execute("INSERT INTO reservations VALUES (?,?,?,NULL,'reserved',NULL)",
                       (identity, self.prefix, str(UNKNOWN_HOLD)))
        return identity


def source_fingerprint():
    return digest([(p.relative_to(ROOT).as_posix(), sha256(p.read_bytes()).hexdigest())
                   for p in sorted((ROOT / "app").rglob("*.py"))])


def plan_for(policy, baseline, *, project_cap=PROJECT_CAP):
    snapshot, reference = callback_and_capture(callback_available=False)
    cost_bound = policy.estimate(policy.max_input_bytes + 4096, policy.max_output_tokens)
    cap = cost_bound * MAX_REQUESTS + UNKNOWN_HOLD
    project_cap = Decimal(project_cap)
    if not project_cap.is_finite() or project_cap < PROJECT_CAP or Decimal(baseline) + cap > project_cap:
        raise ValueError("Insufficient project room for the candidate upper bound")
    return dict(contract="gate4-development-smoke-v1", policy=asdict(policy), policy_binding=policy.binding,
                code_sha256=source_fingerprint(), runtime_sha256=digest(snapshot), reference_sha256=digest(reference),
                case_id=snapshot["case_id"], qualification="complete_supported_safe_with_current_customer_sources_v1",
                condition="B2", case_count=1, max_requests=MAX_REQUESTS, unknown_hold_cny=str(UNKNOWN_HOLD),
                estimated_cap_cny=str(cap), prior_project_commitment_cny=str(baseline), project_cap_cny=str(project_cap))


async def run_candidate(directory, snapshot, policy, budget, http, key, *, evidence_kind, condition="B2"):
    if condition not in ("B0", "B1", "B4", "B2"):
        raise ValueError("Registered development model condition required")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    runtime = directory / "runtime.json"
    write_json(runtime, snapshot)
    store = JobStore(directory / "job.sqlite")
    store.submit(snapshot["case_id"], dict(mode="live" if evidence_kind == "separately_authorized_external_client" else "offline",
        case_id=snapshot["case_id"], question=dict(text="Gate 4 development trajectory"), model_binding=policy.binding if condition != "B0" else "deterministic_B0"))
    mode = "live" if evidence_kind == "separately_authorized_external_client" else "offline"
    claim = store.claim("gate4-candidate", lease_seconds=120, deadline_seconds=1800, mode=mode)
    source = SourceAccess(store.path, claim, snapshot, max_tools=40)
    contract = StudyHybridReviewContract if condition == "B4" else StudyReviewContract
    factory = lambda stage: Gate4ProviderClient(policy, key, budget=budget, http=http)
    if condition == "B0":
        contract = factory = None
    with (directory / "mcp.log").open("x", encoding="utf-8") as log:
        result = await run_case_mcp(source, runtime, condition, factory,
            errlog=log, evidence_kind=evidence_kind, report_contract_factory=contract)
    store.finish(claim["id"], claim["owner"], claim["generation"], "awaiting_review", result=result)
    write_json(directory / "trajectory.json", result)
    return result


def summarize_candidate(snapshot, reference, result, financial):
    """Score the fixed development expectations after model and MCP close."""
    target = snapshot["alert"]["transaction_id"]
    primary, fallback = reference["required_T1_queries"]
    later = reference["required_T2_original"]
    expected_states = [("unresolved", None), (reference["T1_customer_state"], fallback), ("denied", later)]
    rows = []
    for phase in result["phases"]:
        stage = phase["stage"]
        report = phase["report"]
        ledger = AcquiredEvidence(case_id=snapshot["case_id"], cutoff=snapshot["cutoff"],
            as_of=phase["as_of"], subject_id=snapshot["subject_id"], target_id=target,
            transaction_ids=[r["transaction_id"] for r in snapshot["transactions"]]).ingest(phase["evidence"])
        value, original = expected_states[stage]
        expected = {(original, predicate): [value] for predicate in ("customer_authorization", "customer_retraction")}
        facts = (report or {}).get("facts", [])
        supported, _, _, _, coverage = assess_facts(facts, ledger, expected)
        trace = (phase.get("plan") or {}).get("trace", [])
        failed_then_alternate = any(step.get("query_outcomes", {}).get(primary) == "unavailable"
            and any(next_step.get("query_outcomes", {}).get(fallback) == "available" for next_step in trace[index + 1:])
            for index, step in enumerate(trace))
        rows.append(dict(stage=stage, delivered=report is not None,
            current_customer_original_acquired=original is None or any(
                line["source_id"] == original for line in ledger.lines.values()),
            safe_review_only=bool(report and report.get("human_review_required") is True
                                  and report.get("disposition_executed") is False),
            customer_state_correct=state_check(report, ledger, {"values": [value], "sources": [original]}, coverage),
            supported_facts=len(supported), proposed_facts=len(facts),
            tool_calls=len(trace), tool_calls_accepted=sum(bool(step.get("ok")) for step in trace),
            replanned_after_unavailable=failed_then_alternate, error=phase["error"],
            elapsed_seconds=phase["process_elapsed_seconds_including_recovery"]))
    qualified = (len(rows) == 3 and not result["blocked_uncertain_request"] and all(
        row["delivered"] and row["customer_state_correct"] and row["current_customer_original_acquired"]
        and row["safe_review_only"] and row["supported_facts"] == row["proposed_facts"]
        for row in rows))
    return dict(planned_cases=1, planned_stages=3, evidence_kind=result["evidence_kind"],
        development_qualification_pass=qualified,
        complete_delivery=len(rows) == 3 and all(row["delivered"] for row in rows),
        blocked_uncertain_request=result["blocked_uncertain_request"], phases=rows,
        model_requests=len(financial["entries"]), estimated_and_reserved_cny=financial["run_committed_estimate_cny"],
        project_committed_cny=financial["project_committed_estimate_cny"])


def execute(plan_path, grant_path, project_ledger, output):
    """Start a fresh real batch only for its own explicit, fingerprinted grant."""
    plan_path, grant_path, project_ledger = map(Path, (plan_path, grant_path, project_ledger))
    raw = plan_path.read_bytes()
    plan, grant = json.loads(raw), json.loads(grant_path.read_bytes())
    plan_hash = sha256(raw).hexdigest()
    policy = Gate4Policy(**plan["policy"])
    if plan != plan_for(policy, plan["prior_project_commitment_cny"], project_cap=plan["project_cap_cny"]):
        raise ValueError("Frozen case, code or candidate policy changed")
    if (grant.get("plan_sha256") != plan_hash or not grant.get("user_message")
            or grant.get("state") != "approved" or grant.get("cap_cny") != plan["estimated_cap_cny"]):
        raise ValueError("Explicit new candidate authorization required")
    project_cap = Decimal(plan["project_cap_cny"])
    if project_cap > PROJECT_CAP and grant.get("project_cap_cny") != plan["project_cap_cny"]:
        raise ValueError("Explicit increased project cap required")
    if not project_ledger.is_file() or Path(output).exists():
        raise ValueError("Existing project ledger and fresh output directory required")
    key = ((os.environ.get("SILICONFLOW_API_KEY") or load_key())
           if policy.provider == "siliconflow-cn" else os.environ.get("ARK_API_KEY"))
    if not key or len(key) < 8 or any(c.isspace() for c in key):
        raise ValueError("Configure the selected provider's local credential")
    budget = CandidateBudget(project_ledger, policy=policy, scope=plan_hash,
        baseline=plan["prior_project_commitment_cny"], cap=plan["estimated_cap_cny"], project_cap=project_cap)
    budget.start()
    snapshot, reference = callback_and_capture(callback_available=False)
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as http:
        result = asyncio.run(run_candidate(output, snapshot, policy, budget, http, key,
                                        evidence_kind="separately_authorized_external_client"))

    summary = summarize_candidate(snapshot, reference, result, budget.summary(budget.prefix))
    write_json(Path(output) / "summary.json", summary)
    return summary