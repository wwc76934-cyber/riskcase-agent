"""Paid V4 study batch. Requires an explicit grant file; stops at the batch cap.

    python -m scripts.run_v4_study <dataset> <ledger.sqlite> <grant.json> <out_dir>

Grant fields: state="approved", user_message, project_cap_cny, batch_cap_cny,
cases (list of case ids), labels (subset of B1S, B4N, B2V4).
The batch stops before a run if spend so far plus a per-run margin would pass the batch cap,
and stops at once on a run with an unknown (uncertain) request.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from app.evaluation.v31_query_protocol import read_dataset
from app.evaluation.v4_runner import LABELS, project_committed, run_sync
from app.llm.credentials import load_key

PER_RUN_MARGIN = Decimal("0.60")


def main(dataset, ledger, grant_path, out):
    grant = json.loads(Path(grant_path).read_bytes())
    if grant.get("state") != "approved" or not grant.get("user_message"):
        raise SystemExit("Explicit approved grant with the user's message is required")
    project_cap, batch_cap = Decimal(grant["project_cap_cny"]), Decimal(grant["batch_cap_cny"])
    if project_cap > Decimal("50") or not set(grant["labels"]) <= set(LABELS):
        raise SystemExit("Grant outside the 50 CNY project limit or unknown condition label")
    key = load_key()
    if not key:
        raise SystemExit("No local credential")
    if str(dataset) == "heldout":
        from app.evaluation.v4_heldout import candidates
        cases = {s["case_id"]: s for s, _ in candidates()}
    else:
        cases = {entry["case_id"]: snapshot for _, entry, snapshot, _ in read_dataset(dataset)}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    start = project_committed(ledger)
    print("project committed at start", start, "batch cap", batch_cap, flush=True)
    for case_id in grant["cases"]:
        for label in grant["labels"]:
            spent = project_committed(ledger) - start
            if spent + PER_RUN_MARGIN > batch_cap:
                print("STOP: batch cap reached", spent, flush=True)
                return
            folder = out / f"{case_id}__{label}"
            if folder.exists():
                print("skip existing", folder.name, flush=True)
                continue
            baseline = project_committed(ledger)
            scope = f"v4:{grant['id']}:{case_id}:{label}"
            try:
                result = run_sync(folder, cases[case_id], label, key, ledger, scope=scope,
                                  project_cap=project_cap, baseline=baseline)
            except Exception as exc:  # keep the batch inspectable; never retry a paid request
                print("ERROR", case_id, label, type(exc).__name__, str(exc)[:160], flush=True)
                continue
            now = project_committed(ledger)
            print(case_id, label, "complete" if result["complete"] else "partial",
                  "blocked" if result["blocked_uncertain_request"] else "ok",
                  "run_cny", result["financial"]["run_committed_estimate_cny"],
                  "batch_cny", now - start, flush=True)
            if result["blocked_uncertain_request"]:
                print("STOP: uncertain request, no further paid runs", flush=True)
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "ledger", "grant", "out"):
        parser.add_argument(name, type=Path)
    a = parser.parse_args()
    main(a.dataset, a.ledger, a.grant, a.out)
