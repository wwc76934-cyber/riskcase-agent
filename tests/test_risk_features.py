from dataclasses import replace
from datetime import datetime, timedelta, timezone
import random

import pytest

from app.risk.features import FeatureTransaction, iter_history_features, METADATA, NUMERIC_FEATURES, CATEGORICAL_FEATURES


START = datetime(2015, 12, 1)
NOW = datetime(2016, 2, 1, 10)


def tx(identity, time=NOW, amount=1000, user="0", merchant="M", split="train"):
    return FeatureTransaction(identity, user, time, amount, merchant, "Online Transaction", "1234", split)


def features(rows):
    return list(iter_history_features(rows, coverage_start=START))


def test_concrete_demo_excludes_focal_same_time_and_future():
    rows = [tx("past1", NOW - timedelta(hours=1), 2000, merchant="A"),
            tx("past2", NOW - timedelta(minutes=20), 3000), tx("current", NOW, 10000),
            tx("tied", NOW, 50000), tx("future", NOW + timedelta(minutes=10), 90000)]
    result = {row["transaction_id"]: row for row in features(rows)}
    assert result["current"]["count_1h"] == 2
    assert result["current"]["sum_signed_cents_24h"] == 5000
    assert result["current"]["merchant_seen_30d"] == 1
    assert result["tied"]["sum_signed_cents_24h"] == 5000
    assert result["current"]["seconds_since_previous_in_snapshot"] == 1200
    reversed_ties = [*rows[:2], rows[3], rows[2], rows[4]]
    assert result == {row["transaction_id"]: row for row in features(reversed_ties)}


@pytest.mark.parametrize("name,delta", [("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)),
                                       ("7d", timedelta(days=7)), ("30d", timedelta(days=30))])
def test_window_start_included_and_one_second_earlier_excluded(name, delta):
    result = features([tx("outside", NOW - delta - timedelta(seconds=1)),
                       tx("boundary", NOW - delta), tx("current")])[-1]
    assert result[f"count_{name}"] == 1


def test_users_are_isolated_and_no_history_is_explicit():
    result = features([tx("u0"), tx("u1", user="1")])[-1]
    assert result["count_30d"] == result["sum_signed_cents_30d"] == 0
    assert result["has_history_30d"] == 0
    assert result["seconds_since_previous_in_snapshot"] is None
    assert result["history_30d_complete"] == 1


def test_warmup_supplies_history_but_is_not_a_training_target():
    time = datetime(2016, 1, 1)
    result = features([tx("warmup", time - timedelta(hours=1), split="warmup"), tx("train", time)])
    assert len(result) == 1
    assert result[0]["transaction_id"] == "train"
    assert result[0]["count_1h"] == 1
    assert result[0]["history_30d_complete"] == 1


def test_coverage_and_no_history_are_different():
    result = features([tx("early", START + timedelta(days=1))])[0]
    assert result["has_history_30d"] == 0
    assert result["history_30d_complete"] == 0


def test_signed_amounts_stay_exact_and_expired_merchants_are_removed():
    result = features([tx("old", NOW - timedelta(days=30, seconds=1), merchant="old"),
                       tx("negative", NOW - timedelta(hours=2), -123),
                       tx("positive", NOW - timedelta(hours=1), 124),
                       tx("current", merchant="old")])[-1]
    assert result["sum_signed_cents_24h"] == 1
    assert result["count_30d"] == 2
    assert result["merchant_seen_30d"] == 0


def test_prior_validation_transactions_are_history_but_not_future_records():
    time = datetime(2019, 1, 1)
    result = features([tx("validation", time - timedelta(hours=1), 25, split="validation"),
                       tx("test", time, 99, split="test")])[-1]
    assert result["sum_signed_cents_24h"] == 25


def test_appending_future_transactions_cannot_change_existing_features():
    rows = [tx("a", NOW - timedelta(hours=2)), tx("b", NOW)]
    assert features(rows) == features(rows + [tx("future", NOW + timedelta(days=1))])[:2]


def test_unsorted_and_outside_coverage_inputs_fail():
    with pytest.raises(ValueError, match="sorted"):
        features([tx("later"), tx("earlier", NOW - timedelta(seconds=1))])
    with pytest.raises(ValueError, match="sorted"):
        features([tx("user1", user="1"), tx("user0")])
    with pytest.raises(ValueError, match="coverage"):
        features([tx("early", START - timedelta(seconds=1))])


def test_input_types_and_feature_allowlist():
    with pytest.raises(ValueError):
        replace(tx("a"), amount_cents=1.2)
    with pytest.raises(ValueError):
        replace(tx("a"), event_time=NOW.replace(tzinfo=timezone.utc))
    result = features([tx("a")])[0]
    assert set(result) == set(METADATA + NUMERIC_FEATURES + CATEGORICAL_FEATURES)
    assert not {"is_fraud", "Errors?", "merchant_id", "user_id", "split"} & set(NUMERIC_FEATURES + CATEGORICAL_FEATURES)


def test_streaming_features_match_independent_full_history_oracle():
    rng = random.Random(42)
    rows = [tx(str(n), NOW + timedelta(minutes=rng.randrange(0, 60 * 24 * 40)),
               amount=rng.randrange(-500, 2000), user=str(rng.randrange(3)), merchant=f"M{rng.randrange(4)}")
            for n in range(150)]
    rows.sort(key=lambda row: (row.user_id, row.event_time))
    actual = {row["transaction_id"]: row for row in features(rows)}
    for focal in rows:
        result = actual[focal.transaction_id]
        for name, duration in (("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)),
                               ("7d", timedelta(days=7)), ("30d", timedelta(days=30))):
            history = [old for old in rows if old.user_id == focal.user_id
                       and focal.event_time - duration <= old.event_time < focal.event_time]
            assert result[f"count_{name}"] == len(history)
            if name in {"24h", "30d"}:
                assert result[f"sum_signed_cents_{name}"] == sum(old.amount_cents for old in history)
            if name == "30d":
                assert result["merchant_seen_30d"] == int(any(old.merchant_id == focal.merchant_id for old in history))
