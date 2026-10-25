"""Local candidate version/review records. No model calls or deployment endpoint."""

import argparse
import getpass
import json
from pathlib import Path
import sys

from app.risk.review_package import review_package
from app.risk.review_store import ReviewStore


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("register","status","decide","verify"))
    parser.add_argument("--candidate-id",default="case-467716-rule")
    parser.add_argument("--summary",type=Path,default=root / "runs/proposal-live-v1-4de79c571105/summary.json")
    parser.add_argument("--expected-version",type=int)
    parser.add_argument("--request-id")
    parser.add_argument("--package-sha256")
    parser.add_argument("--decision",choices=("approve","reject"))
    parser.add_argument("--note")
    args = parser.parse_args(argv)
    if args.action in {"register","decide"} and (args.expected_version is None or not args.request_id):
        parser.error("Writes require --expected-version and --request-id")
    if args.action=="decide" and not all((args.package_sha256,args.decision,args.note)):
        parser.error("Review requires --package-sha256, --decision and --note")
    path = root / "runs/reviews.sqlite"
    if args.action!="register" and not path.exists():
        print("尚无审阅记录；请先登记候选。",file=sys.stderr)
        return 2
    try:
        store = ReviewStore(path)
        if args.action=="verify": result = store.verify()
        else:
            package = review_package(args.summary)
            actor = "local:"+getpass.getuser()
            if args.action=="register":
                store.register(args.candidate_id,package,expected_version=args.expected_version,actor=actor,request_id=args.request_id)
            elif args.action=="decide":
                store.decide(args.candidate_id,package,expected_version=args.expected_version,package_hash=args.package_sha256,
                    decision=args.decision,actor=actor,note=args.note,request_id=args.request_id)
            # Always return CURRENT effective state, never an old idempotency receipt as approval.
            result = store.effective_status(args.candidate_id,package)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,OSError) as exc:
        print(f"未完成审阅操作：{type(exc).__name__}: {exc}",file=sys.stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
