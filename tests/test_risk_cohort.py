from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from app.risk.cohort import CohortPlan, user_catalog_sha256
from app.risk.cohort_build import build_cohort


def row(stamp="2016-01-01T12:00", **changes):
    date = datetime.fromisoformat(stamp)
    value = {"User": "0", "Card": "0", "Year": str(date.year), "Month": str(date.month),
             "Day": str(date.day), "Time": date.strftime("%H:%M"), "Amount": "$10.00",
             "Merchant Name": "M", "Is Fraud?": "No", "Use Chip": "Online Transaction",
             "MCC": "1234", "Merchant City": "ONLINE", "Merchant State": "", "Zip": "", "Errors?": ""}
    return {**value, **changes}


def build(tmp_path, rows):
    path = tmp_path / "cohort"
    result = build_cohort(path, enumerate(rows, start=1), users=("0", "1"),
                          plan=CohortPlan(size=2), source_id="fixture")
    return path, result


def test_hash_selection_ignores_input_order_and_has_no_label_input():
    plan = CohortPlan(size=3)
    users = [str(n) for n in range(20)]
    assert plan.select_users(users) == plan.select_users(reversed(users))
    assert len(set(plan.select_users(users + users))) == 3
    assert plan.select_users(users) != replace(plan, seed="different-seed").select_users(users)
    assert user_catalog_sha256(users) == user_catalog_sha256(reversed(users))
    assert user_catalog_sha256(users) != user_catalog_sha256(users[:-1])
    with pytest.raises(ValueError):
        plan.select_users(["0"])


@pytest.mark.parametrize("stamp,split", [
    ("2015-11-30T23:59:59", None), ("2015-12-01T00:00:00", "warmup"),
    ("2016-01-01T00:00:00", "train"), ("2017-12-31T23:59:59", "train"),
    ("2018-01-01T00:00:00", "validation"), ("2019-01-01T00:00:00", "test"),
    ("2020-01-01T00:00:00", None),
])
def test_partition_boundaries(stamp, split):
    assert CohortPlan().partition(datetime.fromisoformat(stamp)) == split


def test_invalid_scope_and_aware_time_fail():
    with pytest.raises(ValueError):
        CohortPlan(train_start="2015-12-15T00:00:00")
    with pytest.raises(ValueError):
        CohortPlan(test_start="2017-01-01T00:00:00")
    with pytest.raises(ValueError):
        CohortPlan().partition(datetime.now(timezone.utc))


def test_complete_selected_history_split_and_label_isolation(tmp_path):
    rows = [row("2015-11-30T23:59"), row("2015-12-01T00:00"), row(),
            row("2018-01-01T00:00", **{"Is Fraud?": "Yes"}),
            row("2019-01-01T00:00", Amount="$9999.99"),
            row("2020-01-01T00:00"), row(User="outside")]
    path, result = build(tmp_path, rows)
    assert result["scanned_rows"] == 7
    assert result["selected_rows"] == 4
    assert result["partitions"]["validation"]["fraud_rows"] == 1
    assert result["partitions"]["warmup"]["rows"] == 1
    assert result["training_amount"]["quantiles"]["p100"] == "10"  # test amount cannot affect fit statistics
    assert result["selected_users_without_records"] == ["1"]
    assert not result["class_counts_sufficient"]
    with sqlite3.connect(path / "facts.sqlite") as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(transactions)")}
        assert not {"is_fraud", "Errors?", "available_at"} & columns
        assert db.execute("SELECT source_row FROM transactions ORDER BY source_row").fetchall() == [(2,), (3,), (4,), (5,)]
        with pytest.raises(sqlite3.OperationalError):
            db.execute("SELECT * FROM labels")
    with sqlite3.connect(path / "evaluation-labels.sqlite") as db:
        assert db.execute("SELECT SUM(is_fraud),COUNT(available_at) FROM labels").fetchone() == (1, 0)


def test_duplicate_candidates_retained_and_card_key_scoped(tmp_path):
    path, result = build(tmp_path, [row(), row(**{"Is Fraud?": "Yes"}), row(User="1")])
    assert result["duplicate_candidates"]["groups"] == 1
    assert result["duplicate_candidates"]["extra_rows"] == 1
    assert result["selected_rows"] == 3
    with sqlite3.connect(path / "facts.sqlite") as db:
        assert db.execute("SELECT COUNT(DISTINCT card_id) FROM transactions").fetchone()[0] == 2


def test_signed_exact_amount_quantiles_and_missing_optional(tmp_path):
    _, result = build(tmp_path, [row(Amount="$-1.01"), row(Amount="$0.00"), row(Amount="$10.01")])
    assert result["training_amount"]["quantiles"] == {"p50": "0", "p95": "10.01", "p99": "10.01", "p100": "10.01"}
    assert result["training_amount"]["negative_rows"] == 1
    assert result["partitions"]["train"]["missing_optional"]["Zip"] == 3


@pytest.mark.parametrize("changes", [{"Is Fraud?": "unknown"}, {"Amount": "$0.001"}, {"Month": "99"}])
def test_bad_selected_row_fails_instead_of_silent_history_gap(tmp_path, changes):
    with pytest.raises(ValueError):
        build(tmp_path, [row(**changes)])


def test_source_identity_and_output_overwrite_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="increasing"):
        build_cohort(tmp_path / "bad", [(2, row()), (2, row())], users=("0",), plan=CohortPlan(size=1), source_id="test")
    build(tmp_path, [row()])
    with pytest.raises(FileExistsError):
        build(tmp_path, [row()])


def test_frozen_class_count_gate_accepts_both_classes_in_each_scored_partition(tmp_path):
    rows = [row(stamp, Amount=f"${n}.00", **{"Is Fraud?": "Yes" if n < 30 else "No"})
            for stamp in ("2016-01-01T12:00", "2018-01-01T12:00", "2019-01-01T12:00")
            for n in range(130)]
    _, result = build(tmp_path, rows)
    assert result["class_counts_sufficient"]
    assert result["partition_class_count_gate"] == {"train": True, "validation": True, "test": True}
