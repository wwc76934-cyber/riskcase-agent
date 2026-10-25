"""Query a verified local history snapshot; no model/API key required."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from app.risk.history import UserHistoryTool, check_count_threshold


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--latest", action="store_true", help="Use newest published local snapshot")
    parser.add_argument("--user", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--evidence-limit", type=int, default=5)
    parser.add_argument("--count-threshold", type=int, default=5)
    args = parser.parse_args()
    manifest = args.manifest
    if args.latest:
        root = Path(__file__).resolve().parents[2] / "data" / "history"
        candidates = list(root.glob("snapshot-*/facts/manifest.json"))
        if not candidates:
            parser.error("No published history snapshot; run app.cli.build_history first")
        manifest = max(candidates, key=lambda path: path.stat().st_mtime)
    tool = UserHistoryTool(manifest)
    summary = tool.query(
        user_id=args.user, as_of=datetime.fromisoformat(args.as_of),
        lookback_hours=args.hours, evidence_limit=args.evidence_limit,
    )
    summary["example_rule"] = {
        "threshold": args.count_threshold,
        "decision": check_count_threshold(summary, args.count_threshold),
        "note": "Teaching threshold, not a validated fraud strategy or fraud conclusion.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
