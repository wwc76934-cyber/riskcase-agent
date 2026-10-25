"""Version binding, idempotency, stale writes and local audit consistency."""

from copy import deepcopy
import json
import sqlite3

import pytest

from app.risk.review_store import ReviewConflict, ReviewStore, digest


@pytest.fixture
def store(tmp_path): return ReviewStore(tmp_path / "reviews.sqlite")


def register(store,package=None,**changes):
    args = {"expected_version":0,"actor":"test-author","request_id":"register-one"}
    args.update(changes)
    return store.register("candidate",package or {"rule":"fixture","report":"one"},**args)


def decide(store,state,**changes):
    args = {"expected_version":state["version"],"package_hash":state["package_hash"],"decision":"approve",
        "actor":"test-reviewer","note":"Test-only review","request_id":"approve-one"}
    args.update(changes)
    return store.decide("candidate",state["package"],**args)


def test_register_pending_does_not_approve(store):
    state = register(store)
    assert state["version"]==1 and state["status"]=="pending_review" and not state["deployed"]
    assert not store.effective_status("candidate",state["package"])["approved_for_local_research"]


@pytest.mark.parametrize("decision",["approve","reject"])
def test_local_decision_bound_to_version_hash_and_actor(store,decision):
    state = register(store)
    reviewed = decide(store,state,decision=decision)
    assert reviewed["status"]==("approved" if decision=="approve" else "rejected")
    assert reviewed["decision"]["actor"]=="test-reviewer" and not reviewed["deployed"]
    assert store.verify()["events"]==2


def test_changed_package_invalidates_effective_approval_before_registration(store):
    state = register(store); decide(store,state)
    changed = dict(state["package"],report="two")
    status = store.effective_status("candidate",changed)
    assert not status["approved_for_local_research"]
    assert status["effective_status"]=="source_changed_requires_new_version"


def test_new_revision_starts_pending_and_old_approval_cannot_apply(store):
    old = register(store); decide(store,old)
    new = register(store,{"rule":"changed","report":"two"},expected_version=1,request_id="register-two")
    assert new["version"]==2 and new["status"]=="pending_review" and new["decision"] is None
    with pytest.raises(ReviewConflict,match="stale_version"): decide(store,old,request_id="old-approval")
    assert store.verify()["events"]==3


def test_exact_duplicate_request_is_idempotent_even_after_later_revision(store):
    old = register(store); decision = decide(store,old)
    register(store,{"rule":"changed","report":"two"},expected_version=1,request_id="register-two")
    assert decide(store,old)==decision  # Historical receipt, not current approval.
    assert store.current("candidate")["status"]=="pending_review"
    assert not store.effective_status("candidate",old["package"])["approved_for_local_research"]
    assert store.verify()["events"]==3


def test_registration_retry_does_not_create_duplicate_version(store):
    one = register(store)
    assert register(store)==one
    assert store.verify()["events"]==1


def test_same_request_id_cannot_be_reused_with_changed_command(store):
    register(store)
    with pytest.raises(ReviewConflict,match="idempotency_key"):
        register(store,{"rule":"another"})
    assert store.verify()["events"]==1


def test_two_writers_cannot_both_register_version_zero(store):
    other = ReviewStore(store.path)
    register(store)
    with pytest.raises(ReviewConflict,match="stale_version"): register(other,request_id="other-writer")
    assert other.verify()["events"]==1


def test_wrong_hash_or_changed_source_cannot_be_decided(store):
    state = register(store)
    with pytest.raises(ReviewConflict,match="source_package_changed"): decide(store,state,package_hash="0"*64)
    changed = deepcopy(state); changed["package"]["report"]="changed"
    with pytest.raises(ReviewConflict,match="source_package_changed"): decide(store,changed)
    assert store.current("candidate")["status"]=="pending_review"


def test_revision_can_have_only_one_final_decision(store):
    state = register(store); decide(store,state)
    with pytest.raises(ReviewConflict,match="already_decided"): decide(store,state,decision="reject",request_id="second-decision")


@pytest.mark.parametrize("target",["events","candidates","requests"])
def test_mutated_history_projection_or_receipt_detected(store,target):
    register(store)
    with sqlite3.connect(store.path) as db:
        if target=="events": db.execute("UPDATE events SET previous_hash='corrupted'")
        if target=="candidates": db.execute("UPDATE candidates SET state_json='{}'")
        if target=="requests": db.execute("UPDATE requests SET result_json='{}'")
    with pytest.raises(ReviewConflict,match="audit_"): store.verify()
    with pytest.raises(ReviewConflict,match="audit_"): register(store,expected_version=1,request_id="after-corruption")


def test_empty_rationale_rejected_and_failed_write_is_atomic(store):
    state = register(store)
    with pytest.raises(ValueError,match="rationale"): decide(store,state,note=" ")
    assert store.verify()["events"]==1 and store.current("candidate")["status"]=="pending_review"


def test_returned_state_cannot_mutate_saved_package(store):
    state = register(store)
    state["package"]["report"]="changed"
    assert store.current("candidate")["package"]["report"]=="one"
