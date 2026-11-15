"""Durable three-state audit: raw model decision, guard decision, interventions.

One SQLite row per delivered stage. Rows are append-only; the same
(run_id, stage) cannot be written twice.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

SCHEMA = """CREATE TABLE IF NOT EXISTS guard_audit(
    run_id TEXT NOT NULL, case_id TEXT NOT NULL, condition_label TEXT NOT NULL, stage INTEGER NOT NULL,
    raw_llm_decision TEXT NOT NULL, policy_guard_decision TEXT NOT NULL,
    guard_interventions TEXT NOT NULL, structured_predicates TEXT NOT NULL,
    PRIMARY KEY(run_id, stage))"""


def record(path, run_id, case_id, label, stage, audit):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(SCHEMA)
        db.execute("INSERT INTO guard_audit VALUES (?,?,?,?,?,?,?,?)", (
            run_id, case_id, label, stage,
            json.dumps(audit["raw_llm_decision"], ensure_ascii=False),
            json.dumps(audit["policy_guard_decision"], ensure_ascii=False),
            json.dumps(audit["guard_interventions"], ensure_ascii=False),
            json.dumps(audit["structured_predicates"], ensure_ascii=False)))


def read(path, run_id):
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM guard_audit WHERE run_id=? ORDER BY stage", (run_id,)).fetchall()
    return [{k: (json.loads(r[k]) if k in ("raw_llm_decision", "policy_guard_decision", "guard_interventions",
             "structured_predicates") else r[k]) for k in r.keys()} for r in rows]
