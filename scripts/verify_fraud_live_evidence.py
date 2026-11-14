"""Offline integrity/consistency check; never calls a model or certifies business value."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_pack(directory: Path) -> dict:
    manifest = read_json(directory / "manifest.json")
    hashes = manifest["artifact_sha256"]
    require({"audited-summary.json", "original-summary.json"} <= hashes.keys(),
            "Required summaries are absent from manifest")
    for name, expected in hashes.items():
        require(name not in {"", ".", ".."} and "/" not in name and "\\" not in name
                and ":" not in name, "Manifest contains a non-local filename")
        path = directory / name
        require(path.resolve().parent == directory.resolve(), "Artifact escapes pack")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                f"Artifact hash mismatch: {name}")

    audit = read_json(directory / "audited-summary.json")
    original = read_json(directory / "original-summary.json")
    require(audit["batch_id"] == original["batch_id"], "Batch identity mismatch")
    require(audit["independent_human_validation"] is False
            and audit["causal_memory_gain_proven"] is False,
            "Unsupported validation or causal claim")
    require(original["execution_kind"] == "live_provider", "Unexpected execution kind")
    required_limits = {"observed_development_source_cases", "not_fraud_accuracy",
                       "not_human_efficiency", "no_causal_gain_claim"}
    require(required_limits <= set(original["limits"]), "Missing experiment limitations")

    def index(rows: list) -> dict:
        indexed = {(r["case_id"], r["profile"]): r for r in rows}
        require(len(indexed) == len(rows), "Duplicate case/profile row")
        return indexed

    audited = index(audit["runs"])
    originals = index(original["results"])
    require(audited.keys() == originals.keys(), "Original/audited case sets differ")
    profiles = {key[1] for key in audited}
    require(profiles == {"full", "no_knowledge"}, "Unexpected comparison profiles")
    case_sets = [{key[0] for key in audited if key[1] == profile} for profile in sorted(profiles)]
    require(case_sets[0] == case_sets[1], "Conditions do not contain paired cases")
    require(set(audit["groups"]) == set(original["groups"]) == profiles,
            "Group summaries differ")
    for key, row in audited.items():
        prior = originals[key]
        for field in ("state", "reason", "contract_passed"):
            require(row[field] == prior[field], f"Original result changed: {key}/{field}")
        for field, prior_field in (("requests", "model_turns"),
                                   ("memory_calls", "observed_memory_calls"),
                                   ("current_evidence", "evidence_count")):
            require(row[field] == prior[prior_field], f"Audit disagrees: {key}/{field}")
        require(prior["independent_human_validation"] is False, "Unverified human claim")
        if row["contract_passed"]:
            answer = prior["answer"]
            require(row["state"] == "awaiting_review", "Completed case is not pending review")
            require(set(answer["missing"]) == {"cardholder_authorization",
                    "device_and_authentication", "confirmed_case_outcome"},
                    "Required uncertainty was lost")
            require(all(e.startswith("E") for e in answer["evidence_ids"]),
                    "Memory used as current evidence")

    for profile in sorted(profiles):
        rows = [r for r in audited.values() if r["profile"] == profile]
        group = audit["groups"][profile]
        computed = {"runs": len(rows), "passed": sum(r["contract_passed"] for r in rows)}
        for field in ("requests", "prompt_tokens", "completion_tokens", "memory_calls"):
            computed[field] = sum(r[field] for r in rows)
        for field, value in computed.items():
            require(group[field] == value, f"Group total mismatch: {profile}/{field}")
        for field in ("runs", "passed"):
            require(original["groups"][profile][field] == computed[field],
                    f"Original group total mismatch: {profile}/{field}")
        cost = sum((Decimal(r["estimated_cny"]) for r in rows), Decimal(0))
        require(cost == Decimal(group["estimated_cny"]), "Group cost mismatch")
        elapsed = sum((Decimal(str(r["provider_elapsed_seconds"])) for r in rows), Decimal(0))
        require(abs(elapsed - Decimal(str(group["provider_elapsed_seconds"]))) <= Decimal("0.001"),
                "Group elapsed time mismatch")
    requests = sum(r["requests"] for r in audited.values())
    cost = sum((Decimal(r["estimated_cny"]) for r in audited.values()), Decimal(0))
    require(requests == audit["total_requests"], "Batch request total mismatch")
    require(cost == Decimal(audit["batch_estimated_cny"]), "Batch cost mismatch")
    require(len(audited) <= 8 and requests <= 48 and cost <= Decimal("2"),
            "Recorded batch exceeds authorized limits")
    return {"pack": directory.name, "artifacts": len(hashes), "cases": len(case_sets[0]),
            "runs": len(audited), "requests": requests, "estimated_cny": str(cost),
            "groups": original["groups"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs" / "evidence")
    args = parser.parse_args()
    try:
        packs = [verify_pack(args.evidence_root / name)
                 for name in ("fraud-live-v3", "fraud-live-v4")]
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}))
        return 1
    print(json.dumps({"verified": True, "scope": "portable_manifest_and_summary_consistency_only",
                      "not_verified": ["provider_authenticity", "raw_fact_correctness",
                                       "independent_cases", "business_value"], "packs": packs}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
