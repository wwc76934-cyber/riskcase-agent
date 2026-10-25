"""Verify proposal provenance, model-visible context, rule/replay links and usage."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import re

from app.risk.history import sha256_file
from app.risk.rule_proposal import proposal_context, validate_proposal


def verify(summary_path):
    root = Path(__file__).resolve().parents[1]
    directory = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for name,digest in summary["artifact_sha256"].items():
        if sha256_file(directory / name)!=digest: raise ValueError("Changed proposal artifact")
    source = Path(summary.get("investigation_summary_path",root / "runs/live-case-v1-d1dde6e08986/summary.json")).parent
    training = root / "runs/rule-baseline-ee0d2eaf75f9/frozen-rules.json"
    if sha256_file(source / "summary.json")!=summary["investigation_summary_sha256"] or sha256_file(training)!=summary["training_rule_sha256"]:
        raise ValueError("Source investigation or training policy changed")
    investigation_summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    for name,digest in investigation_summary["artifact_sha256"].items():
        if sha256_file(source / name)!=digest: raise ValueError("Changed source evidence")
    investigation = json.loads((source / "trace.json").read_text(encoding="utf-8"))
    threshold = json.loads(training.read_text(encoding="utf-8"))["thresholds"]["large_positive_amount_cents"]
    expected_context = proposal_context(investigation,amount_threshold=threshold)
    context = json.loads((directory / "context.json").read_text(encoding="utf-8"))
    messages = json.loads((directory / "messages.json").read_text(encoding="utf-8"))
    if context!=expected_context or json.loads(messages[1]["content"])!=context: raise ValueError("Unexpected model-visible context")
    if len(messages)!=3 or messages[-1].get("tool_calls"): raise ValueError("Unexpected proposal execution")
    raw = messages[-1]["content"]
    fence = re.fullmatch(r"```json[ \t]*\r?\n([\s\S]*?)\r?\n```",raw.strip())
    rule = validate_proposal(fence.group(1) if fence else raw,context)
    proposal = json.loads((directory / "proposal.json").read_text(encoding="utf-8"))
    if proposal["rule"]!=rule or proposal["status"]!=summary["status"] or summary["human_approved"] or summary["deployed"]:
        raise ValueError("Proposal rule/state changed")
    if rule:
        replay_dir = Path(summary["replay"]["output_directory"])
        replay = json.loads((replay_dir / "report.json").read_text(encoding="utf-8"))
        frozen_path = replay_dir / "frozen-candidate.json"
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        if frozen["rule"]!=rule or frozen["proposal_sha256"]!=sha256_file(directory / "proposal.json"):
            raise ValueError("Replayed rule differs from validated model proposal")
        if sha256_file(frozen_path)!=replay["frozen_candidate_sha256"] or replay["metrics"]!=summary["replay"]["metrics"]:
            raise ValueError("Replay result changed")
    cost = Decimal(0)
    if summary["client_mode"]=="siliconflow_live":
        audit = json.loads((directory / "usage.json").read_text(encoding="utf-8"))
        policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
        if not audit["requests_sent"]==summary["real_LLM_requests"]==len(audit["request_audit"])==1:
            raise ValueError("Single proposal request budget violated")
        for entry in audit["ledger"]["entries"]:
            if entry["estimated_cny"] is None: cost += Decimal(entry["reserved_cny"])
            else:
                usage = entry["usage"]
                if usage["total_tokens"]!=usage["prompt_tokens"]+usage["completion_tokens"]: raise ValueError("Invalid usage")
                estimate = (Decimal(usage["prompt_tokens"])*Decimal(policy["input_rate_cny_per_million"])
                    +Decimal(usage["completion_tokens"])*Decimal(policy["output_rate_cny_per_million"]))/Decimal(1000000)
                if estimate!=Decimal(entry["estimated_cny"]): raise ValueError("Usage fee estimate differs")
                cost += estimate
        if cost!=Decimal(audit["ledger"]["run_committed_estimate_cny"]): raise ValueError("Run fee total differs")
    elif summary["real_LLM_requests"]!=0: raise ValueError("Fake request mislabeled as real")
    result = {"status":"passed","summary_sha256":sha256_file(summary_path),"client_mode":summary["client_mode"],
        "real_LLM_requests":summary["real_LLM_requests"],"estimated_or_reserved_cny":str(cost),
        "replay_link_checked":rule is not None,"human_approved":False,"deployed":False,
        "limits":"Proposal/context/link/usage consistency only; run SQL replay verifier separately. Not an invoice or rule-benefit endorsement."}
    (directory / "proposal-verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary",type=Path,required=True)
    print(json.dumps(verify(parser.parse_args().summary),indent=2))
