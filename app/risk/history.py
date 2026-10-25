"""Read-only, exact event-time windows over an explicitly scoped snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from app.risk.data_contract import TIME_BASIS, Transaction


@dataclass(frozen=True)
class Coverage:
    users: tuple[str, ...]
    start: datetime
    end: datetime
    source_id: str
    full_source_scanned: bool
    archive_hash_verified: bool

    def __post_init__(self) -> None:
        if not self.users or len(set(self.users)) != len(self.users):
            raise ValueError("coverage requires distinct explicit users")
        if any(not isinstance(user, str) or not user.strip() for user in self.users):
            raise ValueError("invalid user scope")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id required")
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise ValueError("coverage times must be datetimes")
        if self.start.tzinfo is not None or self.end.tzinfo is not None or self.start >= self.end:
            raise ValueError("coverage needs ordered source-local times")
        if type(self.full_source_scanned) is not bool or type(self.archive_hash_verified) is not bool:
            raise ValueError("coverage proof flags must be booleans")

    def as_dict(self) -> dict:
        return {
            "users": list(self.users), "start": self.start.isoformat(), "end": self.end.isoformat(),
            "source_id": self.source_id, "full_source_scanned": self.full_source_scanned,
            "archive_hash_verified": self.archive_hash_verified, "time_basis": TIME_BASIS,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_history_snapshot(directory: Path, transactions: Iterable[Transaction], coverage: Coverage) -> Path:
    """Publish manifest only after all selected facts are written successfully.

    A repeated physical-row reference is deduplicated, but two source rows with
    similar values are NOT assumed to be duplicate business transactions.
    """
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "facts.sqlite"
    duplicates = 0
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("""CREATE TABLE transactions (
            transaction_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_row INTEGER NOT NULL,
            user_id TEXT NOT NULL, card_id TEXT NOT NULL, merchant_id TEXT NOT NULL,
            event_time TEXT NOT NULL, amount TEXT NOT NULL)""")
        for tx in transactions:
            if tx.source_id != coverage.source_id or tx.user_id not in coverage.users:
                raise ValueError("transaction outside source/user scope")
            if not coverage.start <= tx.event_time < coverage.end:
                raise ValueError("transaction outside time scope")
            values = (
                tx.transaction_id, tx.source_id, tx.source_row, tx.user_id, tx.card_id,
                tx.merchant_id, tx.event_time.isoformat(), str(tx.amount),
            )
            existing = connection.execute(
                "SELECT * FROM transactions WHERE transaction_id=?", (tx.transaction_id,)
            ).fetchone()
            if existing is not None:
                if existing != values:
                    raise ValueError("conflicting values for one transaction reference")
                duplicates += 1
                continue
            connection.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", values)
        connection.execute("CREATE INDEX user_time ON transactions(user_id,event_time)")
        count = connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    manifest = {
        "status": "ready", "coverage": coverage.as_dict(), "rows": count,
        "duplicate_references_removed": duplicates, "facts_sha256": sha256_file(database),
        "currency": "unspecified", "business_duplicate_status": "not_established",
        "data_kind": "synthetic", "ingestion_and_label_availability": "not_established",
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class UserHistoryTool:
    """Bind a verified artifact once; callers provide no SQL or filesystem path."""

    def __init__(self, manifest_path: Path):
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("status") != "ready":
            raise ValueError("history artifact is not ready")
        if self.manifest["coverage"].get("time_basis") != TIME_BASIS:
            raise ValueError("unsupported time basis")
        self.database = manifest_path.parent / "facts.sqlite"
        if sha256_file(self.database) != self.manifest["facts_sha256"]:
            raise ValueError("history database hash mismatch")
        raw = self.manifest["coverage"]
        self.coverage = Coverage(
            tuple(raw["users"]), datetime.fromisoformat(raw["start"]), datetime.fromisoformat(raw["end"]),
            raw["source_id"], raw["full_source_scanned"], raw["archive_hash_verified"],
        )

    def query(self, *, user_id: str, as_of: datetime, lookback_hours: int = 24, evidence_limit: int = 20) -> dict:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id required")
        if not isinstance(as_of, datetime) or as_of.tzinfo is not None:
            raise ValueError("as_of must use source-local time")
        if type(lookback_hours) is not int or not 1 <= lookback_hours <= 168:
            raise ValueError("lookback_hours must be in 1..168")
        if type(evidence_limit) is not int or not 1 <= evidence_limit <= 100:
            raise ValueError("evidence_limit must be in 1..100")
        start = as_of - timedelta(hours=lookback_hours)
        reasons = []
        if user_id not in self.coverage.users:
            reasons.append("user_outside_snapshot_scope")
        if start < self.coverage.start or as_of > self.coverage.end:
            reasons.append("window_outside_snapshot_scope")
        if not self.coverage.full_source_scanned:
            reasons.append("source_scan_incomplete")
        if not self.coverage.archive_hash_verified:
            reasons.append("source_integrity_unverified")
        # A fixed SQL template with bound values, read-only DB, no label table.
        connection = sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            cursor = connection.execute(
                "SELECT transaction_id,source_row,event_time,amount,merchant_id FROM transactions "
                "WHERE user_id=? AND event_time>=? AND event_time<? ORDER BY event_time,transaction_id",
                (user_id, start.isoformat(), as_of.isoformat()),
            )
            total = Decimal("0")
            count = 0
            merchants = set()
            evidence = []
            for txid, source_row, event_time, amount, merchant in cursor:
                count += 1
                if count > 100000:
                    raise ValueError("query row budget exceeded")
                total += Decimal(amount)
                merchants.add(merchant)
                if len(evidence) < evidence_limit:
                    evidence.append({
                        "transaction_id": txid, "source_row": source_row, "event_time": event_time,
                        "amount": amount, "merchant_id": merchant,
                    })
        finally:
            connection.close()
        return {
            "tool": "query_user_history", "user_id": user_id, "source_id": self.coverage.source_id,
            "window_start_inclusive": start.isoformat(), "window_end_exclusive": as_of.isoformat(),
            "time_basis": TIME_BASIS, "observed_count": count, "observed_amount_sum": str(total),
            "currency": "unspecified", "distinct_merchants": len(merchants),
            "window_complete_within_source_dataset": not reasons, "incomplete_reasons": reasons,
            "evidence": evidence, "evidence_truncated": count > evidence_limit,
            "facts_sha256": self.manifest["facts_sha256"],
            "limitations": [
                "Completeness is relative to the registered synthetic source, not real business systems.",
                "Event-time coverage does not prove ingestion or label availability.",
                "Amount is source units, not confirmed currency or expected loss.",
            ],
        }


def check_count_threshold(summary: dict, threshold: int) -> str:
    """An incomplete low count is unknown, not evidence that a rule is unmet."""
    if type(threshold) is not int or threshold < 1:
        raise ValueError("threshold must be a positive integer")
    if summary["observed_count"] >= threshold:
        return "met_in_observed_records"
    if summary["window_complete_within_source_dataset"]:
        return "not_met_in_complete_dataset_window"
    return "unknown_incomplete_window"
