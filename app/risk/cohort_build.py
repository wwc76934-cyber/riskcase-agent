"""Write isolated facts/labels and run quality checks on a frozen cohort."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
from typing import Iterable

from app.risk.cohort import MIN_CLASS_COUNTS, CohortPlan
from app.risk.data_contract import parse_tabformer_row


FACT_COLUMNS = ("transaction_id", "source_row", "user_id", "card_id", "event_time", "amount",
                "amount_cents", "merchant_id", "channel", "mcc", "merchant_city", "merchant_state", "zip", "split")
DUPLICATE_COLUMNS = "user_id,card_id,event_time,amount_cents,merchant_id,channel,mcc,merchant_city,merchant_state,zip"


def build_cohort(directory: Path, numbered_rows: Iterable[tuple[int, dict]], *,
                 users: tuple[str, ...], plan: CohortPlan, source_id: str, progress=None) -> dict:
    if len(users) != plan.size or len(set(users)) != len(users):
        raise ValueError("Selected user count does not match frozen plan")
    directory.mkdir(parents=True, exist_ok=False)
    selected = set(users)
    count = scanned = 0
    split_counts, positives = Counter(), Counter()
    missing = {split: Counter() for split in ("warmup", "train", "validation", "test")}
    with closing(sqlite3.connect(directory / "facts.sqlite")) as facts, closing(sqlite3.connect(directory / "evaluation-labels.sqlite")) as labels:
        facts.execute("""CREATE TABLE transactions (
            transaction_id TEXT PRIMARY KEY, source_row INTEGER NOT NULL,
            user_id TEXT NOT NULL, card_id TEXT NOT NULL, event_time TEXT NOT NULL,
            amount TEXT NOT NULL, amount_cents INTEGER NOT NULL, merchant_id TEXT NOT NULL,
            channel TEXT NOT NULL, mcc TEXT NOT NULL, merchant_city TEXT NOT NULL,
            merchant_state TEXT NOT NULL, zip TEXT NOT NULL, split TEXT NOT NULL)""")
        labels.execute("""CREATE TABLE labels (
            transaction_id TEXT PRIMARY KEY, split TEXT NOT NULL,
            is_fraud INTEGER NOT NULL CHECK(is_fraud IN (0,1)), available_at TEXT)""")
        for source_row, row in numbered_rows:
            if type(source_row) is not int or source_row <= scanned:
                raise ValueError("Source row references must be strictly increasing")
            scanned = source_row
            if progress and scanned % 2000000 == 0:
                progress(scanned)
            if row.get("User", "").strip() not in selected:
                continue
            # Validate selected users' years even when outside scope. Parse all
            # records in boundary years before filtering exact timestamps.
            year = int(row["Year"])
            if not int(plan.warmup_start[:4]) <= year <= int(plan.end[:4]):
                continue
            tx, label = parse_tabformer_row(row, source_id=source_id, source_row=source_row)
            split = plan.partition(tx.event_time)
            if split is None:
                continue
            if None in row:
                raise ValueError("Unexpected extra CSV fields")
            cents = tx.amount * Decimal(100)
            if cents != cents.to_integral_value():
                raise ValueError("Amount needs more than 0.01 source-unit precision")
            extras = tuple((row.get(key) or "").strip() for key in (
                "Use Chip", "MCC", "Merchant City", "Merchant State", "Zip"))
            values = (tx.transaction_id, source_row, tx.user_id, tx.card_id,
                      tx.event_time.isoformat(), str(tx.amount), int(cents), tx.merchant_id, *extras, split)
            facts.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            labels.execute("INSERT INTO labels VALUES (?,?,?,NULL)", (tx.transaction_id, split, int(label.is_fraud)))
            split_counts[split] += 1
            positives[split] += int(label.is_fraud)
            for key in ("Use Chip", "MCC", "Merchant City", "Merchant State", "Zip", "Errors?"):
                if not (row.get(key) or "").strip():
                    missing[split][key] += 1
            count += 1
        facts.execute("CREATE INDEX user_time ON transactions(user_id,event_time,source_row)")
        facts.execute("CREATE INDEX split_amount ON transactions(split,amount_cents)")
        facts.commit()
        labels.commit()
        # Physical identities remain intact. Similar observed values are only
        # duplicate candidates: no business transaction ID establishes truth.
        duplicate = facts.execute(f"""SELECT COUNT(*),COALESCE(SUM(n-1),0),COALESCE(MAX(n),0)
            FROM (SELECT COUNT(*) n FROM transactions GROUP BY {DUPLICATE_COLUMNS} HAVING COUNT(*)>1)""").fetchone()
        duplicate_splits = {}
        for split in missing:
            duplicate_splits[split] = facts.execute(f"""SELECT COUNT(*),COALESCE(SUM(n-1),0)
                FROM (SELECT COUNT(*) n FROM transactions WHERE split=?
                GROUP BY {DUPLICATE_COLUMNS} HAVING COUNT(*)>1)""", (split,)).fetchone()
        train_n = split_counts["train"]
        quantiles = {}
        for name, numerator in (("p50", 50), ("p95", 95), ("p99", 99), ("p100", 100)):
            if train_n:
                offset = max(0, (train_n * numerator + 99) // 100 - 1)
                value = facts.execute("SELECT amount_cents FROM transactions WHERE split='train' ORDER BY amount_cents LIMIT 1 OFFSET ?", (offset,)).fetchone()[0]
                quantiles[name] = str(Decimal(value) / 100)
            else:
                quantiles[name] = None
        signs = facts.execute("SELECT COALESCE(SUM(amount_cents<0),0),COALESCE(SUM(amount_cents=0),0),COALESCE(SUM(amount_cents>0),0) FROM transactions WHERE split='train'").fetchone()
        actual_users = {row[0] for row in facts.execute("SELECT DISTINCT user_id FROM transactions")}
        reconciliation = facts.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == labels.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == count == sum(split_counts.values())
        if not reconciliation:
            raise ValueError("Fact/label/split totals disagree")
    partitions = {split: {"rows": split_counts[split], "fraud_rows": positives[split],
                          "normal_rows": split_counts[split] - positives[split],
                          "missing_optional": dict(missing[split]),
                          "duplicate_candidate_groups": duplicate_splits[split][0],
                          "duplicate_candidate_extra_rows": duplicate_splits[split][1]} for split in missing}
    # Operational minimum frozen before source scan; counts are not independent
    # fraud incidents and this threshold is not a statistical power calculation.
    eligibility = {split: split_counts[split] - positives[split] >= MIN_CLASS_COUNTS["negative"] and positives[split] >= MIN_CLASS_COUNTS["positive"]
                   for split in ("train", "validation", "test")}
    return {
        "scanned_rows": scanned, "selected_rows": count, "actual_users": len(actual_users),
        "selected_users_without_records": sorted(selected - actual_users), "partitions": partitions,
        "training_amount": {"quantiles": quantiles, "quantile_method": "nearest rank; signed source amounts",
                            "negative_rows": signs[0], "zero_rows": signs[1], "positive_rows": signs[2]},
        "duplicate_candidates": {"groups": duplicate[0], "extra_rows": duplicate[1], "max_multiplicity": duplicate[2],
                                 "columns": DUPLICATE_COLUMNS.split(","), "policy": "retain_all_pending_business_identity"},
        "partition_class_count_gate": eligibility, "class_count_minimum": dict(MIN_CLASS_COUNTS),
        "counts_reconciled": reconciliation, "class_counts_sufficient": all(eligibility.values()),
        "label_availability_known": False, "ingestion_availability_known": False,
    }
