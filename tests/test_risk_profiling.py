from copy import deepcopy

import pytest

from app.risk.profiling import profile_rows


BASE = {"User": "0", "Card": "0", "Year": "2019", "Month": "12", "Day": "1",
        "Time": "12:30", "Amount": "$10.00", "Merchant Name": "M1", "Is Fraud?": "No",
        "Errors?": "", "Use Chip": "Online Transaction"}


def test_exact_groups_entity_scope_missing_optional_and_signs():
    rows = [dict(BASE), dict(BASE, User="1", Amount="$-2.50", **{"Is Fraud?": "Yes"}),
            dict(BASE, Amount="$0.00", Year="2020")]
    original = deepcopy(rows)
    stats = profile_rows(rows, list(BASE))
    assert rows == original
    assert stats["total_rows"] == stats["valid_rows"] == 3
    assert stats["overall"]["fraud_rows"] == 1
    assert stats["distinct_user_card_pairs"] == 2
    assert stats["by_year"]["2019"]["rows"] == 2
    assert stats["missing_by_column"]["Errors?"] == 3
    assert stats["amount"]["negative_rows"] == stats["amount"]["zero_rows"] == 1
    assert stats["amount"]["min"] == "-2.50"
    assert stats["checks"]["business_duplicates_checked"] is False


@pytest.mark.parametrize("change,reason", [
    ({"Is Fraud?": "unknown"}, "invalid_label"), ({"Amount": "NaN"}, "invalid_amount"),
    ({"Month": "2", "Day": "30"}, "invalid_time"), ({"User": ""}, "missing_required_value"),
    ({None: ["unexpected"]}, "extra_csv_values"),
])
def test_invalid_rows_are_not_counted_as_normal(change, reason):
    stats = profile_rows([dict(BASE), {**BASE, **change}], list(BASE))
    assert stats["valid_rows"] == 1
    assert stats["rejected_rows"] == 1
    assert stats["invalid_reason_counts"][reason] == 1
    assert stats["fraud_fraction_of_valid_rows"] == 0


def test_source_order_is_not_assumed_globally_chronological():
    rows = [dict(BASE, User="0", Day="2"), dict(BASE, User="1", Day="1"),
            dict(BASE, User="0", Day="1")]
    result = profile_rows(iter(rows), list(BASE))
    assert result["ordering"]["global_adjacent_time_decreases"] == 1
    assert result["ordering"]["per_user_time_decreases"] == 1
    assert result["ordering"]["users_in_multiple_blocks"] == 1


def test_empty_and_missing_schema_fail_closed():
    with pytest.raises(ValueError, match="no data"):
        profile_rows([], list(BASE))
    with pytest.raises(ValueError, match="columns"):
        profile_rows([BASE], ["User"])
    with pytest.raises(ValueError, match="columns"):
        profile_rows([BASE], [*BASE, "User"])


def test_all_invalid_has_unknown_rate_not_zero():
    result = profile_rows([dict(BASE, Amount="Infinity")], list(BASE))
    assert result["fraud_fraction_of_valid_rows"] is None
    assert result["overall"]["time_min"] is None
