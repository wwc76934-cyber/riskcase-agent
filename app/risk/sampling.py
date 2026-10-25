"""Bounded discovery sampling; not a population or train/test sampling policy."""

from __future__ import annotations

from collections import Counter
import hashlib
import random

from app.risk.data_contract import EvaluationLabel, Transaction


class UserReservoirSampler:
    """Keep up to k random rows for each of the first m observed users.

    Reservoir sampling lets late rows replace early rows without retaining a
    user's full history. Randomness is reproducible and independent per user.
    Coverage is still restricted to the input scan and first selected users.
    """

    def __init__(self, *, max_users: int, rows_per_user: int, seed: int = 42):
        if type(max_users) is not int or not 1 <= max_users <= 100:
            raise ValueError("max_users must be an integer in 1..100")
        if type(rows_per_user) is not int or not 1 <= rows_per_user <= 1000:
            raise ValueError("rows_per_user must be an integer in 1..1000")
        self.max_users = max_users
        self.rows_per_user = rows_per_user
        self.seed = seed
        self.seen_counts: Counter[str] = Counter()
        self._rows: dict[str, list[tuple[Transaction, EvaluationLabel]]] = {}
        self._rng: dict[str, random.Random] = {}

    def add(self, transaction: Transaction, label: EvaluationLabel) -> None:
        if transaction.transaction_id != label.transaction_id:
            raise ValueError("transaction and label IDs must match")
        user = transaction.user_id
        self.seen_counts[user] += 1
        if user not in self._rows:
            if len(self._rows) >= self.max_users:
                return
            self._rows[user] = []
            digest = hashlib.sha256(f"{self.seed}:{user}".encode()).hexdigest()
            self._rng[user] = random.Random(int(digest, 16))
        bucket = self._rows[user]
        pair = (transaction, label)
        if len(bucket) < self.rows_per_user:
            bucket.append(pair)
        else:
            position = self._rng[user].randrange(self.seen_counts[user])
            if position < self.rows_per_user:
                bucket[position] = pair

    def sample(self) -> list[tuple[Transaction, EvaluationLabel]]:
        return sorted(
            (pair for bucket in self._rows.values() for pair in bucket),
            key=lambda pair: pair[0].source_row,
        )
