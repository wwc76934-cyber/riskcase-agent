"""Label-free history features. For time t, history is [t-window, t)."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import groupby
from typing import Iterable


NUMERIC_FEATURES = (
    "amount_cents", "hour", "weekday", "count_1h", "count_24h", "count_7d", "count_30d",
    "sum_signed_cents_24h", "sum_signed_cents_30d", "merchant_seen_30d", "has_history_30d",
    "history_30d_complete", "seconds_since_previous_in_snapshot",
)
CATEGORICAL_FEATURES = ("channel", "mcc")
METADATA = ("transaction_id", "user_id", "event_time", "split")


@dataclass(frozen=True)
class FeatureTransaction:
    transaction_id: str
    user_id: str
    event_time: datetime
    amount_cents: int
    merchant_id: str
    channel: str
    mcc: str
    split: str

    def __post_init__(self):
        if any(not isinstance(s, str) or not s.strip() for s in (
            self.transaction_id, self.user_id, self.merchant_id)):
            raise ValueError("Transaction/user/merchant identity required")
        if not isinstance(self.event_time, datetime) or self.event_time.tzinfo is not None:
            raise ValueError("Source-local event time required")
        if type(self.amount_cents) is not int:
            raise ValueError("Amount must be integer 0.01 source units")
        if self.split not in {"warmup", "train", "validation", "test"}:
            raise ValueError("Unknown split")


class _Window:
    def __init__(self, duration: timedelta):
        self.duration = duration
        self.rows = deque()
        self.amount_sum = 0
        self.merchants = Counter()

    def expire(self, now: datetime):
        start = now - self.duration
        while self.rows and self.rows[0].event_time < start:
            old = self.rows.popleft()
            self.amount_sum -= old.amount_cents
            self.merchants[old.merchant_id] -= 1
            if not self.merchants[old.merchant_id]:
                del self.merchants[old.merchant_id]

    def add(self, row: FeatureTransaction):
        self.rows.append(row)
        self.amount_sum += row.amount_cents
        self.merchants[row.merchant_id] += 1


def iter_history_features(rows: Iterable[FeatureTransaction], *, coverage_start: datetime):
    """Input sorted by user,time; tied timestamps are scored before any is added.

    All records (including warmup and earlier evaluation-period transactions)
    supply observed history. Only non-warmup records produce feature rows. No
    labels, fitted averages, future transactions or target encodings are read.
    """
    if not isinstance(coverage_start, datetime) or coverage_start.tzinfo is not None:
        raise ValueError("Source-local coverage start required")
    previous_key = None
    previous_user = None
    previous_time = None
    for key, tied_rows in groupby(rows, key=lambda row: (row.user_id, row.event_time)):
        if previous_key is not None and key < previous_key:
            raise ValueError("Input must be sorted by user and event time")
        user, now = key
        if now < coverage_start:
            raise ValueError("Transaction before declared coverage")
        if user != previous_user:
            windows = {"1h": _Window(timedelta(hours=1)), "24h": _Window(timedelta(hours=24)),
                       "7d": _Window(timedelta(days=7)), "30d": _Window(timedelta(days=30))}
            previous_time = None
        for window in windows.values():
            window.expire(now)
        group = list(tied_rows)
        for row in group:
            if row.split == "warmup":
                continue
            yield {
                "transaction_id": row.transaction_id, "user_id": user,
                "event_time": now.isoformat(), "split": row.split,
                "amount_cents": row.amount_cents, "hour": now.hour, "weekday": now.weekday(),
                "channel": row.channel, "mcc": row.mcc,
                **{f"count_{name}": len(window.rows) for name, window in windows.items()},
                "sum_signed_cents_24h": windows["24h"].amount_sum,
                "sum_signed_cents_30d": windows["30d"].amount_sum,
                "merchant_seen_30d": int(windows["30d"].merchants[row.merchant_id] > 0),
                "has_history_30d": int(bool(windows["30d"].rows)),
                "history_30d_complete": int(now - timedelta(days=30) >= coverage_start),
                "seconds_since_previous_in_snapshot": None if previous_time is None else int((now - previous_time).total_seconds()),
            }
        # Deliberately after all outputs at this timestamp: file/row order must
        # not make simultaneous events observe one another.
        for row in group:
            for window in windows.values():
                window.add(row)
        previous_time, previous_user, previous_key = now, user, key
