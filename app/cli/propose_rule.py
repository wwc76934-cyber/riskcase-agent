"""Validated investigation -> bounded template proposal -> host-only offline replay."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from app.cli.investigate_live import local_key
from app.cli.replay_candidate import replay_rule
from app.llm.cost_ledger import CostLedger
from app.llm.fake_client import FakeLLMClient
from app.llm.glm_client import LLMError
from app.llm.siliconflow_client import SiliconFlowClient
from app.risk.history import sha256_file
from app.risk.rule_proposal import proposal_context, request_proposal


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--investigation-summary",type=Path,default=root / "runs/live-case-v1-d1dde6e08986/summary.json")
    args = parser.parse_args(argv)
    source_dir = args.investigation_summary.resolve().parent
    source_summary = json.loads((source_dir / "summary.json").read_text(encoding="utf-8"))
    for name,digest in source_summary["artifact_sha256"].items():
        if sha256_file(source_dir / name)!=digest: raise ValueError("Investigation artifact changed")
    investigation = json.loads((source_dir / "trace.json").read_text(encoding="utf-8"))
    training = root / "runs/rule-baseline-ee0d2eaf75f9/frozen-rules.json"
    threshold = json.loads(training.read_text(encoding="utf-8"))["thresholds"]["large_positive_amount_cents"]
    context = proposal_context(investigation,amount_threshold=threshold)
    key = None
    if args.live:
        try: key = local_key()
        except ValueError:
            print("本项目凭据暂不可读；未发送请求。",file=sys.stderr)
            return 2
        if not key:
            print("请先完成一次性 credentials --set 配置；未发送请求。",file=sys.stderr)
            return 2
    identity = f"proposal-{'live' if args.live else 'fake'}-v1-{uuid4().hex[:12]}"
    directory = root / "runs" / identity
    directory.mkdir()
    (directory / "context.json").write_text(json.dumps(context,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    policy = json.loads((root / "config/siliconflow-live-v1.json").read_text(encoding="utf-8"))
    policy["max_requests"] = 1  # One draft, no format retry or label-guided search loop.
    (directory / "policy.json").write_text(json.dumps(policy,indent=2)+"\n",encoding="utf-8")
    if args.live:
        client = SiliconFlowClient(key,policy=policy,ledger=CostLedger(root / "runs/llm-budget.sqlite"),run_id=identity)
    else:
        ids,refs,conditions = [],[],[]
        for template in context["templates"]:
            additions = [condition for condition in template["conditions"] if condition not in conditions]
            if len(conditions)+len(additions)>4: continue
            ids.append(template["id"]); refs.extend(template["supporting_evidence_ids"]); conditions.extend(additions)
        proposal = {"decision":"propose" if ids else "abstain","template_ids":ids,"evidence_ids":list(dict.fromkeys(refs))}
        client = FakeLLMClient([{"role":"assistant","content":json.dumps(proposal)}])
    replay = None
    try:
        result,messages = request_proposal(client,context)
        if args.live: messages = client.scrub(messages)
        (directory / "messages.json").write_text(json.dumps(messages,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        (directory / "proposal.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
        if result["status"]=="candidate_validated":
            replay = replay_rule(result["rule"],root=root,proposal_sha256=sha256_file(directory / "proposal.json"),
                selection="Model-selected offered evidence-backed templates; development-only, no label-guided proposal iteration." if args.live else "FAKE template combination for pipeline verification; not model discovery.")
    except (LLMError,ValueError,TypeError,OSError):
        result = {"status":"proposal_or_replay_error","human_approved":False,"deployed":False}
    except KeyboardInterrupt:
        result = {"status":"cancelled","human_approved":False,"deployed":False}
    finally:
        if args.live:
            client.close()
            (directory / "usage.json").write_text(json.dumps(client.snapshot(),indent=2)+"\n",encoding="utf-8")
    summary = {"status":result["status"],"client_mode":"siliconflow_live" if args.live else "FAKE_NOT_REAL_LLM",
        "investigation_summary_path":str((source_dir / "summary.json").resolve()),
        "investigation_summary_sha256":sha256_file(source_dir / "summary.json"),"training_rule_sha256":sha256_file(training),
        "real_LLM_requests":client.sent_requests if args.live else 0,"human_approved":False,"deployed":False,
        "replay":replay,"code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "artifact_sha256":{path.name:sha256_file(path) for path in directory.iterdir() if path.is_file()},
        "limits":["Template selection/combination, not open-ended rule discovery.",
            "Development evaluation aggregates remain host-side; not fed back for repeated model optimization.",
            "Offline replay is not approval or deployment."]}
    (directory / "summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output_directory":str(directory),**summary},indent=2))
    return 0 if result["status"] in {"candidate_validated","abstained"} else 1


if __name__ == "__main__": raise SystemExit(main())
