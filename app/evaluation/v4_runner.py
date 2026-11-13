"""Run one case under a V4 study condition against the real provider.

Condition labels:

* ``B1S``  one-shot synthesis (B1) over the streaming transport
* ``B4N``  rule evidence + model report (B4) over the streaming transport
* ``B2V4`` dynamic agent (B2) over the streaming transport with the cost-tier gate

The Policy Guard is applied afterwards by the scorer, on the saved raw report, so
raw model output is always kept. Spend goes through ``CandidateBudget`` against
the project ledger.
"""
import asyncio
from decimal import Decimal
from pathlib import Path

import httpx

from app.evaluation.gate4_candidate import CandidateBudget, MAX_FORMAL_CASE_REQUESTS, UNKNOWN_HOLD
from app.investigation.v3.gate4_provider import Gate4Policy
from app.investigation.v3.gate4_stream import Gate4StreamingClient
from app.investigation.v3.mcp_trajectory import run_case_mcp
from app.investigation.v3.study_delivery import StudyHybridReviewContract, StudyReviewContract
from app.llm.cost_ledger import CostLedger
from app.service.runtime import write_json
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess

LABELS = {"B1S": ("B1", False), "B4N": ("B4", False), "B2V4": ("B2", True),
          "B2S": ("B2", False)}  # B2S: dynamic agent, streaming, no cost-tier gate (ablation of the gate)
POLICY = dict(provider="siliconflow-cn", model="Qwen/Qwen3.8-27B", json_mode="json_schema",
              input_rate="3", output_rate="12", price_source="https://www.siliconflow.cn/pricing",
              price_date="2026-11-10", timeout_seconds=90, expand_hypothesis_branches=True,
              max_input_bytes=64000)  # B1 one-shot stage-2 requests exceeded the 32000-byte default


def policy():
    return Gate4Policy(**POLICY)


def run_cap(pol):
    return pol.estimate(pol.max_input_bytes + 4096, pol.max_output_tokens) * MAX_FORMAL_CASE_REQUESTS + UNKNOWN_HOLD


def project_committed(ledger_path):
    return Decimal(CostLedger(Path(ledger_path)).summary("none")["project_committed_estimate_cny"])


async def run_one(directory, snapshot, label, key, ledger_path, *, scope, project_cap, baseline):
    condition, gate = LABELS[label]
    pol = policy()
    budget = CandidateBudget(ledger_path, policy=pol, scope=scope, baseline=baseline, cap=run_cap(pol),
                             request_limit=MAX_FORMAL_CASE_REQUESTS, project_cap=project_cap)
    budget.start()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    runtime = directory / "runtime.json"
    write_json(runtime, snapshot)
    store = JobStore(directory / "job.sqlite")
    store.submit(snapshot["case_id"], dict(mode="live", case_id=snapshot["case_id"],
        question=dict(text="V4 study trajectory"), model_binding=pol.binding + ":" + label))
    claim = store.claim("v4-study", lease_seconds=120, deadline_seconds=1800, mode="live")
    source = SourceAccess(store.path, claim, snapshot, max_tools=40)
    contract = StudyHybridReviewContract if condition == "B4" else StudyReviewContract
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as http:
        factory = lambda stage: Gate4StreamingClient(pol, key, budget=budget, http=http)
        with (directory / "mcp.log").open("x", encoding="utf-8") as log:
            result = await run_case_mcp(source, runtime, condition, factory, errlog=log,
                evidence_kind="separately_authorized_external_client",
                report_contract_factory=contract, tier_gate=gate)
    store.finish(claim["id"], claim["owner"], claim["generation"], "awaiting_review", result=result)
    result = dict(result, label=label, financial=budget.summary(budget.prefix))
    write_json(directory / "trajectory.json", result)
    return result


def run_sync(*args, **kwargs):
    return asyncio.run(run_one(*args, **kwargs))
