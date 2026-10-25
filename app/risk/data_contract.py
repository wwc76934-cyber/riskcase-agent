"""Separate transaction facts from evaluation-only labels.

TabFormer supplies local calendar fields without a proven timezone or label
confirmation time. Preserve these unknowns instead of inventing UTC or dollars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


TIME_BASIS = "source_local_unspecified"
REQUIRED_COLUMNS = frozenset(
    {"User", "Card", "Year", "Month", "Day", "Time", "Amount", "Merchant Name", "Is Fraud?"}
)


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    source_id: str
    source_row: int
    user_id: str
    card_id: str
    merchant_id: str
    event_time: datetime
    amount: Decimal
    currency: str = "unspecified"
    time_basis: str = TIME_BASIS

    def __post_init__(self) -> None:
        for field in ("transaction_id", "source_id", "user_id", "card_id", "merchant_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if type(self.source_row) is not int or self.source_row < 1:
            raise ValueError("source_row must be a positive integer")
        if not isinstance(self.event_time, datetime) or self.event_time.tzinfo is not None:
            raise ValueError("source-local event_time must be a naive datetime")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise ValueError("amount must be a finite Decimal")
        if self.time_basis != TIME_BASIS or self.currency != "unspecified":
            raise ValueError("this discovery contract must preserve unknown timezone/currency")

    def agent_record(self) -> dict[str, str | int]:
        """Explicit allowlist; never serialize a joined/raw row into Agent input."""
        return {
            "transaction_id": self.transaction_id,
            "source_id": self.source_id,
            "source_row": self.source_row,
            "user_id": self.user_id,
            "card_id": self.card_id,
            "merchant_id": self.merchant_id,
            "event_time": self.event_time.isoformat(),
            "time_basis": self.time_basis,
            "amount": str(self.amount),
            "currency": self.currency,
        }


@dataclass(frozen=True)
class EvaluationLabel:
    transaction_id: str
    is_fraud: bool
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_id, str) or not self.transaction_id.strip():
            raise ValueError("label transaction_id must be a non-empty string")
        if type(self.is_fraud) is not bool:
            raise ValueError("is_fraud must be bool, not a score or string")
        if self.available_at is not None:
            if not isinstance(self.available_at, datetime) or self.available_at.tzinfo is not None:
                raise ValueError("label time must use the same source-local time basis")


def parse_tabformer_row(
    row: dict[str, str], *, source_id: str, source_row: int
) -> tuple[Transaction, EvaluationLabel]:
    """Normalize one row; source_row is assigned BEFORE filtering or sampling.

    This is a physical-row identity, not proof that two rows represent different
    business transactions. Business duplicate detection is a later data gate.
    """
    if not REQUIRED_COLUMNS.issubset(row):
        raise ValueError("required source columns are missing")
    if any(not isinstance(row[key], str) or not row[key].strip() for key in REQUIRED_COLUMNS):
        raise ValueError("required source value is missing")
    try:
        event_time = datetime.strptime(
            f"{row['Year']}-{row['Month']}-{row['Day']} {row['Time']}", "%Y-%m-%d %H:%M"
        )
        # '$' is a source display marker; it does not establish a currency contract.
        amount = Decimal(row["Amount"].strip().removeprefix("$").replace(",", ""))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("invalid event time or amount") from exc
    label_text = row["Is Fraud?"].strip().casefold()
    if label_text not in {"yes", "no"}:
        raise ValueError("unsupported fraud label")
    transaction_id = f"{source_id}#row={source_row}"
    user_id = row["User"].strip()
    transaction = Transaction(
        transaction_id=transaction_id,
        source_id=source_id,
        source_row=source_row,
        user_id=user_id,
        # The source card number is scoped to its user, not a global card identity.
        card_id=f"{user_id}:{row['Card'].strip()}",
        merchant_id=row["Merchant Name"].strip(),
        event_time=event_time,
        amount=amount,
    )
    return transaction, EvaluationLabel(transaction_id, label_text == "yes")


def visible_agent_records(
    transactions: Iterable[Transaction], *, as_of: datetime
) -> list[dict[str, str | int]]:
    """Include the focal event at cutoff; exclude future events and all labels.

    An event's time does not prove ingestion/feature availability at that time.
    This filter is an event-time boundary, not full point-in-time validation.
    """
    if not isinstance(as_of, datetime) or as_of.tzinfo is not None:
        raise ValueError("cutoff must use the same source-local time basis")
    return [tx.agent_record() for tx in transactions if tx.event_time <= as_of]
