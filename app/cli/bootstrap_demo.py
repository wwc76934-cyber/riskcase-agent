"""Create safe offline demo inputs on a fresh checkout; no downloads or keys."""

import argparse
import json
from pathlib import Path

from app.risk.demo_fixture import ensure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("runs/public-demo-data-v1"))
    args = parser.parse_args()
    catalog = ensure(args.directory)
    print(json.dumps({"catalog": str(catalog.resolve()), "data_kind": "self_authored_fixture",
                      "real_LLM_requests": 0, "trained_detector": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
