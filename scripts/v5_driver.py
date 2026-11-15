"""Run a paid study batch to completion, restarting after an uncertain request.

    python -m scripts.v5_driver <dataset> <ledger> <label> <out_dir> <attempts> <batch_cap> <case_id> [<case_id> ...]

``run_v4_study`` stops at the first uncertain request (a read timeout may have been billed). This driver
archives the blocked run folder with a ``_failed<N>`` suffix, writes a new grant with a new id, and starts the
batch again. Each failure keeps its 0.50 CNY hold in the ledger. It gives up after ``attempts`` failures.
"""
import json
from pathlib import Path
import subprocess
import sys

dataset, ledger, label, out, attempts, cap, *cases = sys.argv[1:]
out = Path(out)
attempts = int(attempts)
MSG = "V5 PRD: 15 CNY API budget, no mid-way reports"


def folders():
    return {c: out / f"{c}__{label}" for c in cases}


def state(folder):
    t = folder / "trajectory.json"
    if not t.exists():
        return "missing"
    d = json.loads(t.read_text(encoding="utf-8"))
    return "blocked" if d.get("blocked_uncertain_request") or not d.get("complete") else "ok"


for attempt in range(1, attempts + 1):
    pending = [c for c, f in folders().items() if state(f) != "ok"]
    for c in pending:
        f = folders()[c]
        if f.exists():
            n = 1
            while (out / f"{c}__{label}_failed{n}").exists():
                n += 1
            f.rename(out / f"{c}__{label}_failed{n}")
    if not pending:
        break
    grant = Path(f"runs/grant-driver-{label}-{attempt}-{Path(ledger).stem}.json")
    grant.write_text(json.dumps(dict(id=f"drv{attempt}-{Path(ledger).stem}-{label}", state="approved", user_message=MSG, project_cap_cny="75",
                                     batch_cap_cny=cap, cases=pending, labels=[label])), encoding="utf-8")
    subprocess.run([sys.executable, "-u", "-m", "scripts.run_v4_study", dataset, ledger, str(grant), str(out)], check=False)
print(label, {c: state(f) for c, f in folders().items()})
