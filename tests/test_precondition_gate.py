from app.investigation.v3.precondition_gate import feedback_metrics, split_precondition

ROLES = {"p": "primary", "c": "corroborating", "f": "fallback", "free": None}


def test_nothing_paid_beyond_the_primary_before_the_primary_is_seen():
    assert split_precondition(["p", "f"], ROLES, {}, False) == (["p"], [("f", "primary_in_same_call")])
    assert split_precondition(["f"], ROLES, {}, False) == ([], [("f", "primary_not_observed")])
    assert split_precondition(["p", "free"], ROLES, {}, False) == (["p", "free"], [])


def test_clear_primary_stops_followup_and_unclear_primary_unlocks_it():
    seen = {"p": {"status": "available"}}
    assert split_precondition(["c", "f"], ROLES, seen, False) == ([], [("c", "primary_clear"), ("f", "primary_clear")])
    assert split_precondition(["c", "f"], ROLES, seen, True) == (["c", "f"], [])
    gone = {"p": {"status": "unavailable"}}
    assert split_precondition(["f"], ROLES, gone, True) == (["f"], [])


def test_already_read_sources_are_never_deferred():
    seen = {"p": {}, "c": {}}
    assert split_precondition(["c"], ROLES, seen, False) == (["c"], [])


def test_feedback_metrics():
    stages = [dict(primary_followup_needed=True, paid_followup_read=True, followup_after_primary_turn=True, deferred=0),
              dict(primary_followup_needed=True, paid_followup_read=True, followup_after_primary_turn=False, fallback_read_without_need=False, deferred=0),
              dict(primary_followup_needed=False, paid_followup_read=True, followup_after_primary_turn=False, fallback_read_without_need=True, deferred=0),
              dict(primary_followup_needed=False, paid_followup_read=False, deferred=2)]
    m = feedback_metrics(stages)
    assert m["opportunities"] == 2 and m["feedback_driven"] == 1 and m["true_feedback_driven_rate"] == 0.5
    assert m["paid_followup_reads"] == 3 and m["premature_followup_reads"] == 1 and m["blocked"] == 2
