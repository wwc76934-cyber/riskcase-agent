"""Read-only ledger summary: totals and any reservation not settled from usage."""
from contextlib import closing
from decimal import Decimal
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "runs/llm-budget.sqlite"
with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
    rows = db.execute("SELECT id,run_id,reserved_cny,estimated_cny,status FROM reservations").fetchall()
    holds = db.execute("SELECT run_id,amount_cny,reason FROM budget_holds").fetchall()
total = sum((Decimal(e if e is not None else r) for _, _, r, e, _ in rows), Decimal(0)) + sum((Decimal(h[1]) for h in holds), Decimal(0))
print("reservations", len(rows), "holds", len(holds), "committed incl. holds", total)
for rid, run, r, e, s in rows:
    if s != "estimated_from_usage":
        print("UNSETTLED", run[:70], r, e, s)
