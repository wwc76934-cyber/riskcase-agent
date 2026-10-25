"""Run one bounded mixed task; fixed, evidence-only and tool Agent share validators."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from app.cli.investigate_live import local_key
from app.llm.cost_ledger import CostLedger
from app.llm.siliconflow_client import SiliconFlowClient
from app.risk.history import sha256_file
from app.risk.mixed_investigation import CHECKS, MixedQuestion, MixedTools, ScriptedMixedClient, preload, run_fixed, run_mixed
from app.risk.source_merchant_pack import write_json


def execute(root, directory, manifest, question, *, arm, live=False):
    if arm not in ("fixed", "evidence_only", "agent") or (arm == "fixed" and live): raise ValueError("Explicit supported arm required")
    tools = MixedTools(manifest)
    if not "2018-01-01" <= tools.manifest["cutoff"] < "2019-01-01": raise ValueError("Development cases only")
    policy = json.loads((root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "case").mkdir()
    for name in ("manifest.json", "facts.sqlite"): shutil.copyfile(manifest.parent / name, directory / "case" / name)
    tools = MixedTools(directory / "case/manifest.json")
    write_json(directory / "question.json", asdict(question)); write_json(directory / "policy.json", policy)
    client = None
    if arm == "fixed": result = run_fixed(tools, question)
    else:
        if arm == "evidence_only": preload(tools, question)
        if live:
            key = local_key()
            if not key: raise ValueError("Saved credential unavailable")
            run_id = "mixed-" + hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:24]
            client = SiliconFlowClient(key, policy=policy, ledger=CostLedger(root / "runs/llm-budget.sqlite"), run_id=run_id)
        else: client = ScriptedMixedClient(question, tools.evidence if arm == "evidence_only" else None)
        try: result = run_mixed(client, tools, question, client_mode="siliconflow_live" if live else "SCRIPTED_NOT_REAL_LLM", preloaded=arm == "evidence_only")
        finally:
            if live:
                client.close(); write_json(directory / "usage.json", client.snapshot())
        if live: result = client.scrub(result)
    report = result.pop("report_markdown")
    if report is not None: (directory / "report.md").write_text(report, encoding="utf-8")
    write_json(directory / "trace.json", result)
    summary = {key: value for key, value in result.items() if key not in ("messages", "evidence", "tool_trace")}
    summary.update(arm=arm, question=asdict(question), real_LLM_requests=client.sent_requests if live else 0,
                   cache_hits=sum(row.get("cache_hit", False) for row in tools.trace),
                   artifact_sha256={str(p.relative_to(directory)).replace("\\", "/"): sha256_file(p) for p in directory.rglob("*") if p.is_file()},
                   data_kind="synthetic", policy_approved=False, deployed=False)
    write_json(directory / "summary.json", summary)
    return summary


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-manifest", type=Path, default=root / "runs/case-v1-f4a58a923f32/case/manifest.json")
    parser.add_argument("--checks", nargs="+", choices=CHECKS, default=list(CHECKS))
    parser.add_argument("--min-records", type=int, default=10)
    parser.add_argument("--arm", choices=("fixed", "evidence_only", "agent"), default="fixed")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    directory = args.output or root / "runs" / ("mixed-" + uuid4().hex)
    summary = execute(root, directory, args.case_manifest, MixedQuestion(args.checks, args.min_records), arm=args.arm, live=args.live)
    print(json.dumps({"output_directory": str(directory), **summary}, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "completed_validated" else 1


if __name__ == "__main__": raise SystemExit(main())
