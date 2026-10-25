from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from app.risk.data_contract import (
    EvaluationLabel, Transaction, parse_tabformer_row, visible_agent_records,
)
from app.risk.sampling import UserReservoirSampler
from app.cli.data_gate import write_jsonl


def source_row(**overrides: str) -> dict[str, str]:
    row = {
        "User": "7", "Card": "0", "Year": "2019", "Month": "3", "Day": "2",
        "Time": "10:30", "Amount": "$12.30", "Merchant Name": "merchant-A", "Is Fraud?": "Yes",
    }
    row.update(overrides)
    return row


def parsed(index: int = 1, user: str = "7") -> tuple[Transaction, EvaluationLabel]:
    return parse_tabformer_row(source_row(User=user), source_id="test-snapshot", source_row=index)


def test_money_identity_and_unknowns_are_preserved():
    tx, label = parsed()
    assert tx.amount == Decimal("12.30")
    assert tx.card_id == "7:0"
    assert tx.transaction_id == label.transaction_id == "test-snapshot#row=1"
    assert label.is_fraud is True
    assert label.available_at is None
    assert tx.currency == "unspecified"
    assert tx.event_time.tzinfo is None


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "not-money"])
def test_nonfinite_or_invalid_amount_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_tabformer_row(source_row(Amount=bad), source_id="test", source_row=1)


@pytest.mark.parametrize("overrides", [{"Day": "31", "Month": "2"}, {"User": ""}, {"Is Fraud?": "unknown"}])
def test_bad_date_missing_identity_and_unknown_label_do_not_become_normal(overrides):
    with pytest.raises(ValueError):
        parse_tabformer_row(source_row(**overrides), source_id="test", source_row=1)


def test_missing_required_column_fails():
    row = source_row()
    del row["Is Fraud?"]
    with pytest.raises(ValueError):
        parse_tabformer_row(row, source_id="test", source_row=1)


def test_negative_amount_is_preserved_instead_of_called_fraud():
    tx, label = parse_tabformer_row(source_row(Amount="$-3.00", **{"Is Fraud?": "No"}), source_id="test", source_row=1)
    assert tx.amount == Decimal("-3.00")
    assert label.is_fraud is False


def test_agent_allowlist_drops_labels_and_untrusted_extra_fields():
    row = source_row(**{"secret": "do not copy", "label_reason": "answer", "is_fraud": "True"})
    tx, _ = parse_tabformer_row(row, source_id="test", source_row=1)
    record = tx.agent_record()
    assert set(record) == {
        "transaction_id", "source_id", "source_row", "user_id", "card_id", "merchant_id",
        "event_time", "time_basis", "amount", "currency",
    }
    assert "secret" not in str(record) and "label_reason" not in str(record)


def test_cutoff_includes_current_event_but_hides_future_events():
    tx, _ = parsed()
    future = replace(tx, transaction_id="future", event_time=datetime(2020, 1, 1))
    records = visible_agent_records([tx, future], as_of=tx.event_time)
    assert [r["transaction_id"] for r in records] == [tx.transaction_id]


def test_mixing_utc_with_unknown_local_time_is_rejected():
    tx, _ = parsed()
    with pytest.raises(ValueError):
        visible_agent_records([tx], as_of=datetime(2020, 1, 1, tzinfo=timezone.utc))


def test_shared_card_number_is_scoped_by_user():
    a, _ = parsed(user="a")
    b, _ = parsed(user="b")
    assert a.card_id != b.card_id


def test_mispaired_label_cannot_enter_sampler():
    tx, _ = parsed()
    sampler = UserReservoirSampler(max_users=1, rows_per_user=2)
    with pytest.raises(ValueError):
        sampler.add(tx, EvaluationLabel("another-row", True))
    assert not sampler.sample()


def test_sampling_retains_minor_user_and_is_reproducible():
    # A 100-row user must not crowd out a 3-row user in a discovery case set.
    pairs = [parsed(i, "large") for i in range(1, 101)] + [parsed(i, "small") for i in range(101, 104)]
    left = UserReservoirSampler(max_users=2, rows_per_user=3, seed=42)
    right = UserReservoirSampler(max_users=2, rows_per_user=3, seed=42)
    for pair in pairs:
        left.add(*pair)
        right.add(*pair)
    assert left.sample() == right.sample()
    assert len(left.sample()) == 6
    assert {tx.user_id for tx, _ in left.sample()} == {"large", "small"}
    assert any(tx.source_row > 3 and tx.user_id == "large" for tx, _ in left.sample())
    assert all(tx.transaction_id == label.transaction_id for tx, label in left.sample())


def test_user_cap_is_explicit_and_extra_users_counted():
    sampler = UserReservoirSampler(max_users=1, rows_per_user=2)
    sampler.add(*parsed(1, "a"))
    sampler.add(*parsed(2, "b"))
    assert len(sampler.seen_counts) == 2
    assert {tx.user_id for tx, _ in sampler.sample()} == {"a"}


def test_jsonl_hash_binds_actual_artifact_bytes(tmp_path):
    path = tmp_path / "facts.jsonl"
    expected = [{"kind": "合成数据", "value": "12.30"}]
    digest = write_jsonl(path, expected)
    payload = path.read_bytes()
    assert digest == hashlib.sha256(payload).hexdigest()
    assert b"\r\n" not in payload
    assert [json.loads(line) for line in payload.decode("utf-8").splitlines()] == expected
