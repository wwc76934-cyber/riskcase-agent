"""Immutable case inputs, journal replay and a separately runnable job worker."""

from dataclasses import asdict, dataclass
import errno
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from app.risk.history import sha256_file
from app.risk.merchant_research import MerchantQuestion, MemoizedMerchantTools, ScriptedMerchantClient, run_merchant_research
from app.risk.mixed_investigation import MixedQuestion, MixedTools, ScriptedMixedClient, run_mixed
from app.risk.intent_router import NaturalQuestion
from app.service.store import Blocked, Cancelled, Conflict, JobStore, LeaseLost, digest, encoded
from app.tools.case_tools import CaseTools


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(encoded(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@dataclass(frozen=True)
class Settings:
    repo: Path
    directory: Path
    catalog: dict
    token: str
    allow_live: bool = False

    def __post_init__(self):
        if not isinstance(self.token, str) or not 32 <= len(self.token) <= 256 or not self.token.isascii():
            raise ValueError("Strong local bearer token required")
        self.directory.mkdir(parents=True, exist_ok=True)


def fingerprint(repo):
    paths = [*sorted((repo / "app/service").glob("*.py")), repo / "app/agent/react_agent.py",
             repo / "app/risk/merchant_research.py", repo / "app/risk/merchant_stats.py",
             repo / "app/tools/case_tools.py", repo / "app/llm/siliconflow_client.py",
             repo / "app/llm/cost_ledger.py", repo / "app/risk/investigation.py",
             repo / "app/llm/glm_client.py", repo / "app/llm/fake_client.py",
             repo / "app/risk/case_snapshot.py", repo / "app/risk/history.py",
             repo / "app/risk/mixed_investigation.py",
             repo / "app/risk/intent_router.py",
             repo / "app/risk/planner_v2.py",
             repo / "config/siliconflow-live-v1.json", repo / "requirements-service.txt"]
    return digest({str(path.relative_to(repo)).replace("\\", "/"): sha256_file(path) for path in paths})


class ResearchService:
    def __init__(self, settings, store=None):
        self.settings = settings
        self.store = store or JobStore(settings.directory / "jobs.sqlite")

    def submit(self, request_key, *, case_id, question, mode="offline", task_type="merchant"):
        if mode not in ("offline", "live") or (mode == "live" and not self.settings.allow_live):
            raise ValueError("live_mode_not_enabled")
        if task_type not in ("merchant", "mixed", "natural", "natural_v2"): raise ValueError("unsupported_task_type")
        question = {"mixed": MixedQuestion, "merchant": MerchantQuestion, "natural": NaturalQuestion, "natural_v2": NaturalQuestion}[task_type](**question)
        if case_id not in self.settings.catalog: raise ValueError("unknown_case_id")
        original = Path(self.settings.catalog[case_id])
        files = {name: sha256_file(original.parent / name) for name in ("manifest.json", "facts.sqlite")}
        key = digest(files)
        parent = self.settings.directory / "inputs"
        parent.mkdir(exist_ok=True)
        frozen = parent / key
        if not frozen.exists():
            staging = parent / ("staging-" + uuid4().hex)
            staging.mkdir()
            try:
                for name in files: shutil.copyfile(original.parent / name, staging / name)
                if any(sha256_file(staging / name) != expected for name, expected in files.items()):
                    raise ValueError("case_changed_during_copy")
                check = CaseTools(staging / "manifest.json", allow_merchant_distribution=True)
                if not "2018-01-01" <= check.manifest["cutoff"] < "2019-01-01":
                    raise ValueError("development_period_only")
                try: staging.rename(frozen)
                except OSError as exc:
                    # Windows reports EEXIST; POSIX may report ENOTEMPTY when
                    # another publisher already installed the same directory.
                    # All other I/O failures propagate. Hashes below still
                    # verify the winner before a job can be submitted.
                    if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not frozen.is_dir():
                        raise
            finally:
                # Only the explicitly created staging directory is removed.
                if staging.exists():
                    for name in ("manifest.json", "facts.sqlite"):
                        (staging / name).unlink(missing_ok=True)
                    staging.rmdir()
        if any(sha256_file(frozen / name) != expected for name, expected in files.items()):
            raise ValueError("frozen_case_changed")
        spec = {"case_id": case_id, "input_key": key, "files": files, "mode": mode,
                "question": asdict(question), "runtime_fingerprint": fingerprint(self.settings.repo)}
        if task_type != "merchant": spec["task_type"] = task_type
        return self.store.submit(request_key, spec)

    def artifact(self, identity, name):
        if name not in ("report.md", "trace.json", "summary.json", "usage.json", "plan.json", "route.json"): raise KeyError("artifact_not_allowed")
        job = self.store.get(identity)
        if job["result"] is None: raise Conflict("result_not_published")
        result = job["result"]
        if digest(result) != job["result_hash"]: raise Conflict("result_changed")
        directory = (self.settings.directory / result["directory"]).resolve()
        if not directory.is_relative_to(self.settings.directory.resolve()): raise Conflict("invalid_result_path")
        summary_path = directory / "summary.json"
        if sha256_file(summary_path) != result["summary_sha256"]: raise Conflict("summary_changed")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        path = directory / name
        if name != "summary.json" and name not in summary["artifact_sha256"]: raise KeyError("artifact_not_published")
        if name != "summary.json" and sha256_file(path) != summary["artifact_sha256"].get(name):
            raise Conflict("artifact_changed")
        return path

    def review(self, identity, **payload):
        # Check the submitted materials immediately before version-bound acceptance.
        for name in ("report.md", "trace.json", "summary.json"): self.artifact(identity, name)
        return self.store.review(identity, **payload)

    def confirm_plan(self, identity, **payload):
        # Idempotent replay remains possible after the second phase published a report.
        saved = self.store.plan(identity)
        if saved is None: raise Conflict("plan_not_published")
        if self.store.get(identity)["state"] == "awaiting_plan_confirmation":
            for name in ("plan.json", "route.json", "summary.json"): self.artifact(identity, name)
            if json.loads(self.artifact(identity, "plan.json").read_text(encoding="utf-8")) != saved["proposal"]:
                raise Conflict("plan_materials_differ")
        return self.store.confirm_plan(identity, **payload)


class GuardedTools:
    def __init__(self, wrapped, checkpoint): self.wrapped, self.checkpoint = wrapped, checkpoint
    def __getattr__(self, key): return getattr(self.wrapped, key)
    def dispatch(self, name, arguments):
        self.checkpoint()
        result = self.wrapped.dispatch(name, arguments)
        self.checkpoint()
        return result


class JournalClient:
    def __init__(self, store, claim, base, checkpoint, *, after_saved=None):
        self.store, self.claim, self.base, self.checkpoint = store, claim, base, checkpoint
        self.calls = self.new_calls = self.replayed = 0
        self.after_saved = after_saved

    def chat(self, messages, tools=None):
        self.checkpoint()
        self.calls += 1
        args = (self.claim["id"], self.claim["owner"], self.claim["generation"], self.calls)
        response = self.store.begin_call(*args, digest({"messages": messages, "tools": tools or []}))
        if response is not None:
            self.replayed += 1
            return response
        self.new_calls += 1
        # A crash/exception after intent is deliberately unresolved, never auto-retried.
        try:
            response = self.base.chat(messages, tools)
        except (Exception, KeyboardInterrupt):
            audit = self.base.snapshot() if hasattr(self.base, "snapshot") else {"simulation": True}
            self.store.uncertain_call(*args, audit)
            raise Blocked("unresolved_call") from None
        if hasattr(self.base, "scrub"): response = self.base.scrub(response)
        audit = self.base.snapshot() if hasattr(self.base, "snapshot") else {"simulation": True}
        self.store.complete_call(*args, response, audit)
        if self.after_saved: self.after_saved(self.calls)
        self.checkpoint()
        return response


class Worker:
    def __init__(self, service, *, client_factory=None, lease_seconds=90, after_saved=None):
        self.service, self.store = service, service.store
        self.owner, self.lease_seconds = uuid4().hex, lease_seconds
        self.client_factory, self.after_saved = client_factory, after_saved

    def step(self):
        claim = self.store.claim(self.owner, lease_seconds=self.lease_seconds)
        if claim is None: return None
        identity, generation = claim["id"], claim["generation"]
        base = None
        def checkpoint():
            self.store.checkpoint(identity, self.owner, generation, lease_seconds=self.lease_seconds)
        try:
            spec = json.loads(claim["spec"])
            if digest(spec) != claim["spec_hash"]: raise Blocked("case_input_changed")
            if spec["runtime_fingerprint"] != fingerprint(self.service.settings.repo): raise Blocked("runtime_changed")
            if spec["mode"] == "live" and not self.service.settings.allow_live: raise Blocked("live_mode_not_enabled")
            inputs = self.service.settings.directory / "inputs" / spec["input_key"]
            if any(sha256_file(inputs / name) != value for name, value in spec["files"].items()): raise Blocked("case_input_changed")
            directory = self.service.settings.directory / "jobs" / identity / f"attempt-{generation}"
            (directory / "case").mkdir(parents=True, exist_ok=False)
            for name in spec["files"]: shutil.copyfile(inputs / name, directory / "case" / name)
            if any(sha256_file(directory / "case" / name) != value for name, value in spec["files"].items()): raise Blocked("case_copy_changed")
            if spec.get("task_type") in ("natural", "natural_v2"):
                from app.service.natural import process
                process(self, claim, spec, directory, checkpoint)
                return self.store.get(identity)
            mixed = spec.get("task_type", "merchant") == "mixed"
            question = (MixedQuestion if mixed else MerchantQuestion)(**spec["question"])
            tools = GuardedTools((MixedTools if mixed else MemoizedMerchantTools)(directory / "case/manifest.json"), checkpoint)
            policy = json.loads((self.service.settings.repo / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
            mode = "siliconflow_live" if spec["mode"] == "live" else "SCRIPTED_NOT_REAL_LLM"
            if self.client_factory:
                if spec["mode"] != "offline": raise Blocked("test_factory_offline_only")
                base = self.client_factory(question)
            elif spec["mode"] == "offline": base = (ScriptedMixedClient if mixed else ScriptedMerchantClient)(question)
            else:
                from app.cli.investigate_live import local_key
                from app.llm.cost_ledger import CostLedger
                from app.llm.siliconflow_client import SiliconFlowClient
                key = local_key()
                if not key: raise Blocked("saved_credential_unavailable")
                base = SiliconFlowClient(key, policy=policy, ledger=CostLedger(self.service.settings.repo / "runs/llm-budget.sqlite"), run_id=identity)
                # Re-anchor the client's monotonic budget to the persisted task deadline.
                # A restarted HTTP client must not obtain a fresh 180-second allowance.
                remaining = max(0, min(policy["run_deadline_seconds"], claim["deadline"] - self.store.clock()))
                base.started = base.clock() - (policy["run_deadline_seconds"] - remaining)
            client = JournalClient(self.store, claim, base, checkpoint, after_saved=self.after_saved)
            result = (run_mixed(client, tools, question, client_mode=mode, max_requests=6) if mixed else
                      run_merchant_research(client, tools, question, client_mode=mode, max_requests=6, allow_format_repair=True))
            checkpoint()
            if hasattr(base, "scrub"): result = base.scrub(result)
            report = result.pop("report_markdown")
            write_json(directory / "trace.json", result)
            write_json(directory / "question.json", asdict(question))
            write_json(directory / "policy.json", policy)
            if hasattr(base, "snapshot"): write_json(directory / "usage.json", base.snapshot())
            if report is not None: (directory / "report.md").write_text(report, encoding="utf-8")
            summary = {key: result[key] for key in ("status", "client_mode", "findings", "tool_attempts", "client_requests", "first_attempt", "format_repair", "failure_stage", "maximum_client_requests")}
            summary.update(data_kind="synthetic", question=asdict(question), job_id=identity, generation=generation,
                           task_type="mixed" if mixed else "merchant",
                           format_repair_enabled=True, evidence_preloaded=False, memoization_enabled=True,
                           cache_hits=sum(row.get("cache_hit", False) for row in tools.trace),
                           successful_query_executions=sum(row["result"]["ok"] and not row.get("cache_hit", False) for row in tools.trace),
                           new_client_requests=client.new_calls, replayed_client_responses=client.replayed,
                           real_LLM_requests_this_attempt=base.sent_requests if spec["mode"] == "live" else 0,
                           runtime_fingerprint=spec["runtime_fingerprint"], policy_approved=False, deployed=False,
                           artifact_sha256={str(path.relative_to(directory)).replace("\\", "/"): sha256_file(path) for path in directory.rglob("*") if path.is_file()})
            if mixed: summary.update(arm="agent", real_LLM_requests=base.sent_requests if spec["mode"] == "live" else 0)
            write_json(directory / "summary.json", summary)
            payload = {"directory": str(directory.relative_to(self.service.settings.directory)).replace("\\", "/"),
                       "summary_sha256": sha256_file(directory / "summary.json"), "status": result["status"],
                       "new_client_requests": client.new_calls, "replayed_client_responses": client.replayed,
                       "policy_approved": False, "deployed": False}
            checkpoint()
            self.store.finish(identity, self.owner, generation, "awaiting_review" if result["status"] == "completed_validated" else "failed",
                              reason=None if result["status"] == "completed_validated" else result["status"], result=payload)
        except Cancelled:
            self.store.finish(identity, self.owner, generation, "cancelled", reason="cancel_requested")
        except LeaseLost:
            pass  # A successor owns the job. This worker may not publish anything.
        except Exception as exc:
            safe_reasons = {"runtime_changed", "live_mode_not_enabled", "case_input_changed", "case_copy_changed",
                            "saved_credential_unavailable", "test_factory_offline_only", "task_deadline",
                            "replay_request_changed", "journal_hash_mismatch", "unresolved_call", "request_limit"}
            reason = str(exc) if isinstance(exc, Blocked) and str(exc) in safe_reasons else "worker_failed_inspect_journal"
            try: self.store.finish(identity, self.owner, generation, "blocked", reason=reason)
            except LeaseLost: pass
        finally:
            if base is not None and hasattr(base, "close"): base.close()
        return self.store.get(identity)
