"""Two-phase natural task: propose, await scope confirmation, then code-only evidence."""

from dataclasses import asdict
import json

from app.risk.history import sha256_file
from app.risk.intent_router import NaturalQuestion, POLICY, ScriptedRouter, proposal, route
from app.risk.mixed_investigation import MixedQuestion, MixedTools, run_fixed
from app.service.store import Blocked, digest


def process(worker, claim, spec, directory, checkpoint):
    from app.service.runtime import GuardedTools, JournalClient, write_json
    service, identity, generation = worker.service, claim["id"], claim["generation"]
    question = NaturalQuestion(**spec["question"])
    route_function, proposal_function, scripted = route, proposal, ScriptedRouter
    if spec["task_type"] == "natural_v2":
        from app.risk.planner_v2 import route as route_function, proposal as proposal_function, ScriptedPlanner as scripted
    saved = service.store.plan(identity)
    write_json(directory / "natural-input.json", asdict(question))
    policy = json.loads((service.settings.repo / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    write_json(directory / "policy.json", policy)
    if saved is None:
        base = None
        try:
            if worker.client_factory:
                if spec["mode"] != "offline": raise Blocked("test_factory_offline_only")
                base = worker.client_factory(question)
            elif spec["mode"] == "offline": base = scripted(question)
            else:
                from app.cli.investigate_live import local_key
                from app.llm.cost_ledger import CostLedger
                from app.llm.siliconflow_client import SiliconFlowClient
                key = local_key()
                if not key: raise Blocked("saved_credential_unavailable")
                base = SiliconFlowClient(key, policy=policy, ledger=CostLedger(service.settings.repo / "runs/llm-budget.sqlite"), run_id=identity)
                remaining = max(0, min(policy["run_deadline_seconds"], claim["deadline"] - service.store.clock()))
                base.started = base.clock() - (policy["run_deadline_seconds"] - remaining)
            client = JournalClient(service.store, claim, base, checkpoint, after_saved=worker.after_saved)
            result = route_function(client, question)
            if hasattr(base, "scrub"): result = base.scrub(result)
            write_json(directory / "route.json", result)
            if hasattr(base, "snapshot"): write_json(directory / "usage.json", base.snapshot())
            proposed = proposal_function(question, result["route"], case_key=spec["input_key"], runtime=spec["runtime_fingerprint"]) if result["route"] and result["route"]["decision"] == "execute" else None
            if proposed: write_json(directory / "plan.json", proposed)
            decision = result["route"]["decision"] if result["route"] else None
            state = {"execute": "awaiting_plan_confirmation", "clarify": "needs_clarification", "reject": "out_of_scope"}.get(decision, "failed")
            summary = {"status": result["status"], "phase": "scope_proposal", "route": result["route"], "failure_code": result["failure_code"],
                       "plan_hash": digest(proposed) if proposed else None, "tool_attempts": 0,
                       "client_requests": client.calls, "new_client_requests": client.new_calls, "replayed_client_responses": client.replayed,
                       "real_LLM_requests": base.sent_requests if spec["mode"] == "live" else 0,
                       "client_mode": "siliconflow_live" if spec["mode"] == "live" else "KEYWORD_SCRIPT_NOT_REAL_LLM"}
            publish(service, claim, directory, summary, state, checkpoint, proposed)
        finally:
            if base is not None and hasattr(base, "close"): base.close()
        return
    if not saved["confirmed"]: raise Blocked("scope_confirmation_required")
    proposed = saved["proposal"]
    expected = proposal_function(question, proposed["route"], case_key=spec["input_key"], runtime=spec["runtime_fingerprint"])
    if expected != proposed: raise Blocked("scope_binding_changed")
    mixed = MixedQuestion(proposed["route"]["checks"])
    tools = GuardedTools(MixedTools(directory / "case/manifest.json"), checkpoint)
    result = run_fixed(tools, mixed)
    report = result.pop("report_markdown")
    write_json(directory / "trace.json", result)
    write_json(directory / "question.json", asdict(mixed))
    write_json(directory / "plan.json", proposed)
    if report is not None: (directory / "report.md").write_text(report, encoding="utf-8")
    summary = {k: v for k, v in result.items() if k not in ("messages", "tool_trace", "evidence")}
    summary.update(arm="fixed", phase="confirmed_evidence", question=asdict(mixed), plan_hash=saved["plan_hash"],
                   scope_confirmation="authenticated_local_token", cache_hits=sum(bool(r.get("cache_hit")) for r in tools.trace),
                   real_LLM_requests=0, new_client_requests=0, replayed_client_responses=0)
    publish(service, claim, directory, summary, "awaiting_review" if result["status"] == "completed_validated" else "failed", checkpoint)


def publish(service, claim, directory, summary, state, checkpoint, proposed=None):
    from app.service.runtime import write_json
    import json
    summary.update(task_type=json.loads(claim["spec"])["task_type"], job_id=claim["id"], generation=claim["generation"], data_kind="synthetic",
                   policy_approved=False, deployed=False,
                   artifact_sha256={str(p.relative_to(directory)).replace("\\", "/"): sha256_file(p) for p in directory.rglob("*") if p.is_file()})
    write_json(directory / "summary.json", summary)
    payload = {"directory": str(directory.relative_to(service.settings.directory)).replace("\\", "/"),
               "summary_sha256": sha256_file(directory / "summary.json"), "status": summary["status"],
               "phase": summary["phase"], "plan_hash": summary["plan_hash"], "policy_approved": False, "deployed": False}
    checkpoint()
    service.store.finish(claim["id"], claim["owner"], claim["generation"], state, result=payload, proposal=proposed)
