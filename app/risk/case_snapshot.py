"""Host-side projection: one validation alert and prior same-user facts, no labels."""

from contextlib import closing
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3

from app.risk.history import sha256_file


COLUMNS = ("transaction_id", "source_row", "user_id", "card_id", "event_time", "amount_cents", "merchant_id", "channel", "mcc")


def local_time(value):
    if not isinstance(value, str):
        raise ValueError("Source-local timestamp text required")
    result = datetime.fromisoformat(value)
    if result.tzinfo is not None:
        raise ValueError("Timezone not established; use source-local timestamps")
    return result


def export_case(directory: Path, *, source_database: Path, expected_source_sha256: str,
                transaction_id: str, alert: dict, coverage_start: str, coverage_end: str,
                full_source_scanned: bool, source_report_sha256: str):
    if sha256_file(source_database) != expected_source_sha256:
        raise ValueError("Source facts changed")
    if type(full_source_scanned) is not bool:
        raise ValueError("Explicit coverage proof required")
    if alert.get("transaction_id") != transaction_id or isinstance(alert.get("score"), bool) or not isinstance(alert.get("score"), (int, float)) or not math.isfinite(alert["score"]) or not 0 <= alert["score"] <= 1:
        raise ValueError("Bound target and finite model ranking score required")
    start, end = local_time(coverage_start), local_time(coverage_end)
    with closing(sqlite3.connect(source_database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        target = db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE transaction_id=? "
            "AND split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'", (transaction_id,)).fetchone()
        if target is None:
            raise ValueError("Target must be a 2018 development-validation transaction")
        target = dict(target)
        cutoff = local_time(target["event_time"])
        if not start <= cutoff < end:
            raise ValueError("Target outside declared source coverage")
        history_start = max(start, cutoff-timedelta(days=30))
        rows = db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE user_id=? "
            "AND event_time>=? AND event_time<? ORDER BY event_time,source_row LIMIT 100001",
            (target["user_id"], history_start.isoformat(), cutoff.isoformat())).fetchall()
        if len(rows) > 100000:
            raise ValueError("Case history row budget exceeded")
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "facts.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE transactions (transaction_id TEXT PRIMARY KEY,source_row INTEGER,
            user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT)""")
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)", [tuple(row) for row in rows]+[tuple(target[key] for key in COLUMNS)])
    manifest = {"status": "case_ready", "version": 1, "data_kind": "synthetic",
        "target_transaction_id": transaction_id, "user_id": target["user_id"],
        "cutoff": cutoff.isoformat(), "history_start": history_start.isoformat(),
        "maximum_lookback_hours": 720, "prior_rows": len(rows), "full_source_scanned": full_source_scanned,
        "projection_sha256": sha256_file(path), "source_facts_sha256": expected_source_sha256,
        "source_report_sha256": source_report_sha256, "alert": alert,
        "time_basis": "source_local_timezone_unknown", "currency": "unspecified",
        "limits": ["Synthetic-source facts, not real-world completeness.",
            "Physical source rows retained; business deduplication not established.",
            "No ingestion/label-availability proof; equal-time peers excluded.",
            "No label table, unrelated user, future transaction or filesystem/SQL tool exposed.",
            "Interface/projection isolation is not an operating-system permission sandbox."]}
    result = directory / "manifest.json"
    result.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return result
