"""Record hashes of the guard, scorer and held-out cases before any held-out model run.

If a later run reports different hashes for these files, the held-out result is not blind.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from app.evaluation.v4_heldout import candidates
from app.investigation.contracts import digest

FILES = ["app/investigation/v3/policy_guard.py", "app/evaluation/v4_score.py",
         "app/evaluation/v4_heldout.py", "app/investigation/v3/tier_gate.py"]


def main(out="docs/evidence/heldout-freeze.json"):
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    cases = [(s["case_id"], digest(s), digest(r)) for s, r in candidates()]
    freeze = dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  files_sha256_lf={f: sha(f) for f in FILES}, cases=len(cases), case_digest=digest(cases),
                  note="Guard table and scorer frozen before the first held-out model request.")
    Path(out).write_text(json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return freeze


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
