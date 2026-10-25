import sqlite3

import pytest

from app.risk.rule_baseline import DailyReviewer, RuleThresholds, confusion_metrics, evaluate_validation, fit_thresholds, score_transaction


def record(identity="a", **changes):
    return {"transaction_id": identity, "event_time": "2018-01-01T10:00:00", "split": "validation",
            "amount_cents": 100, "count_1h": 0, "has_history_30d": 1, "merchant_seen_30d": 1,
            "is_fraud": False, **changes}


def test_fit_uses_train_only_and_positive_amounts_with_nearest_rank():
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE features (amount_cents INTEGER,count_1h INTEGER,split TEXT)")
        db.executemany("INSERT INTO features VALUES (?,?,?)", [(x, n, "train") for x, n in (
            (-100, 0), (0, 0), (100, 1), (200, 2), (300, 3), (400, 4))])
        db.execute("INSERT INTO features VALUES (999999,999,'validation')")
        db.execute("INSERT INTO features VALUES (99999999,9999,'test')")
        result = fit_thresholds(db, percentile=75)
        assert result.large_positive_amount_cents == 300
        assert result.high_prior_count_1h == 3
        db.execute("DELETE FROM features WHERE split='train'")
        with pytest.raises(ValueError):
            fit_thresholds(db)


def test_rules_strict_boundaries_and_unknown_history():
    limits = RuleThresholds(100, 2)
    result = score_transaction(record(amount_cents=100, count_1h=2), limits)
    assert not result["amount_only"]["alert"]
    assert result["history_rules"]["score"] == 0
    result = score_transaction(record(amount_cents=101, count_1h=3, merchant_seen_30d=0), limits)
    assert result["history_rules"]["score"] == 3
    assert result["history_rules"]["alert"]
    result = score_transaction(record(has_history_30d=0, merchant_seen_30d=0), limits)
    assert result["history_rules"]["score"] == 0
    with pytest.raises(ValueError):
        score_transaction(record(has_history_30d=0, merchant_seen_30d=1), limits)


def test_confusion_counts_precision_and_recall():
    result = confusion_metrics(rows=100, positives=10, alerts=20, true_positives=8)
    assert (result["false_positives"], result["false_negatives"], result["true_negatives"]) == (12, 2, 78)
    assert result["precision"] == 0.4
    assert result["recall"] == 0.8


def test_undefined_metrics_remain_unknown():
    result = confusion_metrics(rows=100, positives=0, alerts=0, true_positives=0)
    assert result["precision"] is None
    assert result["recall"] is None
    with pytest.raises(ValueError):
        confusion_metrics(rows=10, positives=3, alerts=5, true_positives=4)


def reviewed_ids(rows):
    result, queues = evaluate_validation(rows, RuleThresholds(100, 2), daily_budget=2)
    return result, {name: [r["transaction_id"] for r in queue] for name, queue in queues.items()}, queues


def test_daily_budget_is_per_day_and_ties_ignore_labels_and_input_order():
    rows = [record(str(n), event_time=f"2018-01-0{1+n//4}T10:00:00", is_fraud=n % 2 == 0) for n in range(8)]
    result, ids, queues = reviewed_ids(rows)
    assert result["strategies"]["history_rules"]["daily_review"]["reviewed_or_alerted"] == 4
    assert ids == reviewed_ids(list(reversed(rows)))[1]
    assert ids == reviewed_ids([{**row, "is_fraud": not row["is_fraud"]} for row in rows])[1]
    assert all("is_fraud" not in row for queue in queues.values() for row in queue)
    assert all(len(queue) == 4 for queue in queues.values())


def test_threshold_alerts_and_budget_review_are_distinct_policies():
    result, _, _ = reviewed_ids([record("normal"), record("fraud", is_fraud=True)])
    stats = result["strategies"]["history_rules"]
    assert stats["threshold_policy"]["reviewed_or_alerted"] == 0
    assert stats["threshold_policy"]["precision"] is None
    assert stats["daily_review"]["reviewed_or_alerted"] == 2
    assert stats["daily_review"]["reviewed_without_threshold_hit"] == 2


@pytest.mark.parametrize("split", ["train", "test", "warmup"])
def test_final_test_and_wrong_partitions_are_rejected(split):
    with pytest.raises(ValueError, match="validation only"):
        reviewed_ids([record(split=split)])


@pytest.mark.parametrize("arguments", [(0, 2), (100, -1), (100, 2, 4)])
def test_invalid_thresholds_fail(arguments):
    with pytest.raises(ValueError):
        RuleThresholds(*arguments)


def test_invalid_labels_and_empty_evaluation_fail():
    with pytest.raises(ValueError):
        reviewed_ids([record(is_fraud="No")])
    with pytest.raises(ValueError):
        reviewed_ids([])
    with pytest.raises(ValueError):
        DailyReviewer(budget=0)
