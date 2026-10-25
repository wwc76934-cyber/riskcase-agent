"""Label-independent customer selection and frozen event-time partitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Iterable


MIN_CLASS_COUNTS = {"positive": 30, "negative": 100}


def user_catalog_sha256(users: Iterable[str]) -> str:
    payload = json.dumps(sorted(set(users)), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class CohortPlan:
    size: int = 500
    seed: str = "riskcase-cohort-v1-seed42"
    warmup_start: str = "2015-12-01T00:00:00"
    train_start: str = "2016-01-01T00:00:00"
    validation_start: str = "2018-01-01T00:00:00"
    test_start: str = "2019-01-01T00:00:00"
    end: str = "2020-01-01T00:00:00"

    def __post_init__(self):
        if type(self.size) is not int or not 1 <= self.size <= 2000 or not self.seed:
            raise ValueError("Invalid cohort size/seed")
        times = [datetime.fromisoformat(getattr(self, name)) for name in (
            "warmup_start", "train_start", "validation_start", "test_start", "end")]
        if any(t.tzinfo is not None or t.isoformat() != getattr(self, name) for t, name in zip(times, (
            "warmup_start", "train_start", "validation_start", "test_start", "end"))):
            raise ValueError("Use canonical source-local timestamps")
        if any(left >= right for left, right in zip(times, times[1:])):
            raise ValueError("Split boundaries must be strictly increasing")
        if (times[1] - times[0]).days < 30:
            raise ValueError("At least 30 days of warmup required")

    def select_users(self, user_ids: Iterable[str]) -> tuple[str, ...]:
        candidates = set(user_ids)
        if any(not isinstance(user, str) or not user.strip() for user in candidates):
            raise ValueError("Invalid user identity")
        if len(candidates) < self.size:
            raise ValueError("Insufficient distinct users")
        def rank(user):
            return hashlib.sha256(f"{self.seed}\0{user}".encode()).hexdigest(), user
        return tuple(sorted(candidates, key=rank)[:self.size])

    def partition(self, event_time: datetime) -> str | None:
        if not isinstance(event_time, datetime) or event_time.tzinfo is not None:
            raise ValueError("Source-local datetime required")
        stamp = event_time.isoformat()
        if not self.warmup_start <= stamp < self.end:
            return None
        if stamp < self.train_start:
            return "warmup"
        if stamp < self.validation_start:
            return "train"
        if stamp < self.test_start:
            return "validation"
        return "test"
