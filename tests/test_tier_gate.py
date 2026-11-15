from app.investigation.v3.tier_gate import DEFERRED, deferral_receipts, planning_metrics, split_batch, tier_of

CATALOG = [dict(source_id="free", cost_units=0), dict(source_id="p2", cost_units=2),
           dict(source_id="p4", cost_units=4), dict(source_id="p1", cost_units=1)]


def test_tiers():
    assert [tier_of(c) for c in (0, 1, 2, 3, 4, 9)] == [0, 1, 2, 3, 3, 3]


def test_single_paid_tier_and_free_reads_are_untouched():
    assert split_batch(["free", "p2"], CATALOG, set()) == (["free", "p2"], [])
    assert split_batch(["p4"], CATALOG, set()) == (["p4"], [])


def test_dearer_paid_source_is_deferred_in_the_same_call():
    allowed, deferred = split_batch(["free", "p4", "p2"], CATALOG, set())
    assert allowed == ["free", "p2"] and deferred == ["p4"]


def test_three_paid_tiers_release_one_tier_at_a_time():
    allowed, deferred = split_batch(["p4", "p2", "p1"], CATALOG, set())
    assert allowed == ["p1"] and deferred == ["p4", "p2"]


def test_already_observed_sources_never_block_or_get_deferred():
    allowed, deferred = split_batch(["p2", "p4"], CATALOG, {"p2"})
    assert allowed == ["p2", "p4"] and deferred == []


def test_receipt_costs_nothing_and_is_structured():
    receipt, = deferral_receipts(["p4"], CATALOG)
    assert receipt["status"] == DEFERRED and receipt["cost_charged"] == 0 and receipt["tier"] == 3


def test_metrics_count_credits_blocks_and_feedback_driven_turns():
    trace = [dict(model_turn=1, ok=True, source_ids=["free", "p2"], deferred_source_ids=["p4"]),
             dict(model_turn=2, ok=True, source_ids=["p4"])]
    m = planning_metrics(trace, CATALOG)
    assert m == dict(credits=6, premature_blocked=1, feedback_driven_turns=1, sources_read=3)
