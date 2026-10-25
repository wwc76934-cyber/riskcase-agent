"""Streaming source inventory, not a training sample or a detector.

Retain exact group counts and extrema, never all transactions. Labels are used
only for dataset eligibility statistics; this module is not an Agent tool.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.risk.data_contract import REQUIRED_COLUMNS


def _group() -> dict:
    return {"rows": 0, "fraud_rows": 0, "time_min": None, "time_max": None}


def _update(group: dict, stamp: str, positive: bool) -> None:
    group["rows"] += 1
    group["fraud_rows"] += int(positive)
    group["time_min"] = min(group["time_min"] or stamp, stamp)
    group["time_max"] = max(group["time_max"] or stamp, stamp)


def profile_rows(rows: Iterable[dict[str, str]], columns: list[str], progress=None) -> dict:
    if len(columns) != len(set(columns)) or not REQUIRED_COLUMNS.issubset(columns):
        raise ValueError("Missing required or duplicate CSV columns")
    total = valid = negative = zero = positive = 0
    missing = Counter()
    invalid = Counter()
    years, months, users, channels = {}, {}, {}, {}
    cards, merchants = set(), set()
    amount_min = amount_max = None
    overall = _group()
    previous_global = None
    previous_by_user = {}
    global_backwards = user_backwards = 0
    previous_user = None
    user_blocks = Counter()
    for row in rows:
        total += 1
        if progress and total % 2000000 == 0:
            progress(total)
        # Empty optional Errors?/Zip are recorded, not silently discarded.
        for field in columns:
            if not isinstance(row.get(field), str) or not row[field].strip():
                missing[field] += 1
        reasons = []
        if None in row:
            reasons.append("extra_csv_values")
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in REQUIRED_COLUMNS):
            reasons.append("missing_required_value")
        label = str(row.get("Is Fraud?", "")).strip().casefold()
        if label not in {"yes", "no"}:
            reasons.append("invalid_label")
        try:
            hour, minute = row["Time"].split(":")
            event_time = datetime(int(row["Year"]), int(row["Month"]), int(row["Day"]), int(hour), int(minute))
            stamp = event_time.isoformat(timespec="minutes")
        except (KeyError, TypeError, ValueError, AttributeError):
            reasons.append("invalid_time")
        try:
            amount = Decimal(row["Amount"].strip().removeprefix("$").replace(",", ""))
            if not amount.is_finite():
                raise ValueError("nonfinite")
        except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
            reasons.append("invalid_amount")
        if reasons:
            invalid.update(set(reasons))
            continue
        valid += 1
        fraud = label == "yes"
        user = row["User"].strip()
        card = (user, row["Card"].strip())
        merchant = row["Merchant Name"].strip()
        cards.add(card)
        merchants.add(merchant)
        negative += int(amount < 0)
        zero += int(amount == 0)
        positive += int(amount > 0)
        amount_min = amount if amount_min is None else min(amount_min, amount)
        amount_max = amount if amount_max is None else max(amount_max, amount)
        _update(overall, stamp, fraud)
        for mapping, key in ((users, user), (years, stamp[:4]), (months, stamp[:7]),
                             (channels, row.get("Use Chip", "").strip() or "<missing>")):
            _update(mapping.setdefault(key, _group()), stamp, fraud)
        if previous_global is not None and stamp < previous_global:
            global_backwards += 1
        if user in previous_by_user and stamp < previous_by_user[user]:
            user_backwards += 1
        if user != previous_user:
            user_blocks[user] += 1
        previous_global, previous_user = stamp, user
        previous_by_user[user] = stamp
    if total == 0:
        raise ValueError("CSV has no data rows")
    # Independent aggregation reconciliation must hold before publishing output.
    for mapping in (users, years, months, channels):
        if sum(g["rows"] for g in mapping.values()) != valid or sum(g["fraud_rows"] for g in mapping.values()) != overall["fraud_rows"]:
            raise ValueError("Grouped counts failed reconciliation")
    return {
        "total_rows": total, "valid_rows": valid, "rejected_rows": total - valid,
        "invalid_reason_counts": dict(invalid), "missing_by_column": dict(missing),
        "columns": columns, "overall": overall,
        "fraud_fraction_of_valid_rows": overall["fraud_rows"] / valid if valid else None,
        "distinct_users": len(users), "distinct_user_card_pairs": len(cards),
        "distinct_merchants": len(merchants),
        "users_with_fraud": sum(g["fraud_rows"] > 0 for g in users.values()),
        "amount": {"negative_rows": negative, "zero_rows": zero, "positive_rows": positive,
                   "min": str(amount_min) if amount_min is not None else None,
                   "max": str(amount_max) if amount_max is not None else None,
                   "unit": "source units; currency not established"},
        "ordering": {"global_adjacent_time_decreases": global_backwards,
                     "per_user_time_decreases": user_backwards,
                     "users_in_multiple_blocks": sum(n > 1 for n in user_blocks.values()),
                     "scope": "valid rows only; decrease means earlier event time, not a proven error"},
        "by_year": dict(sorted(years.items())), "by_month": dict(sorted(months.items())),
        "by_user": dict(sorted(users.items())), "by_channel": dict(sorted(channels.items())),
        "checks": {"group_totals_reconciled": True,
                   "business_duplicates_checked": False,
                   "label_availability_known": False,
                   "ingestion_availability_known": False},
    }
