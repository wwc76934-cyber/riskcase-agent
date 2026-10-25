"""Neural baseline -> unchanged candidate -> frozen capacity study; no approval action."""

import argparse
import json
from pathlib import Path

from app.risk.capacity_v2 import evaluate, freeze, materialize


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "plan", "evaluate", "verify", "all"))
    parser.add_argument("--directory", type=Path, default=root / "runs/capacity-neural-v2")
    parser.add_argument("--neural-run", type=Path, default=root / "runs/neural-v2-development-01")
    args = parser.parse_args()
    from scripts.verify_capacity_v2 import verify
    if args.action == "freeze": result = freeze(root, args.directory, neural_run=args.neural_run)
    elif args.action == "plan": result = materialize(root, args.directory)
    elif args.action == "evaluate": result = evaluate(root, args.directory)
    elif args.action == "verify": result = verify(root, args.directory)
    else:
        for name, action in (("freeze", lambda: freeze(root, args.directory, neural_run=args.neural_run)),
                             ("plan", lambda: materialize(root, args.directory)), ("evaluate", lambda: evaluate(root, args.directory)),
                             ("verify", lambda: verify(root, args.directory))):
            result = action()
            print(json.dumps({"stage": name, "status": "completed"}), flush=True)
    print(json.dumps(result if args.action == "verify" else {"directory": str(args.directory), "status": result.get("status"), "baseline_kind": "neural_v2_ensemble"}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
