"""Primary-to-fallback precondition gate (U3).

Roles come from the channel of each source record:

* ``registered-callback``      primary channel
* ``linked-record-archive``    corroborating source (requires the primary original)
* ``app-attestation``          fallback channel (the dearest)

A fallback or corroborating source may be read only after the primary channel has been observed, and only if
the primary result needs follow-up: the primary was unavailable, or its statement is still unresolved by the
rule reader. A clear primary result stops further paid reads. The host does not choose which source to read; it
defers a request that breaks the condition and returns a zero-cost receipt so the agent can plan again.
"""
ROLE = {"registered-callback": "primary", "linked-record-archive": "corroborating", "app-attestation": "fallback"}
DEFERRED = "DEFERRED_BY_HOST_POLICY"


def roles_of(snapshot):
    return {r["source_id"]: ROLE.get(r.get("channel_ref")) for r in snapshot["records"]}


def split_precondition(requested, roles, queried, primary_needs_followup):
    """Return ``(allowed, deferred)``; ``deferred`` is a list of (source_id, reason).

    ``queried`` maps source ids already queried (acquired or unavailable) to receipts.
    ``primary_needs_followup`` is True when the observed primary result was unavailable or unresolved.
    """
    primary_ids = [i for i, role in roles.items() if role == "primary"]
    primary_observed = any(i in queried for i in primary_ids)
    allowed, deferred = [], []
    for source_id in requested:
        role = roles.get(source_id)
        if source_id in queried or role not in ("fallback", "corroborating"):
            allowed.append(source_id)
        elif not primary_observed and not any(p in requested for p in primary_ids):
            deferred.append((source_id, "primary_not_observed"))
        elif not primary_observed:
            deferred.append((source_id, "primary_in_same_call"))
        elif not primary_needs_followup:
            deferred.append((source_id, "primary_clear"))
        else:
            allowed.append(source_id)
    return allowed, deferred


def feedback_metrics(stages):
    """Aggregate U3 metrics from per-stage records.

    Each stage record: ``primary_followup_needed`` (bool or None when the primary was never observed),
    ``paid_followup_read`` (a fallback or corroborating source was read), ``followup_after_primary_turn`` (that
    read happened in a turn after the primary result), ``fallback_read_without_need`` (a fallback or
    corroborating source was read although the primary was clear or unobserved), ``deferred`` (count).
    """
    need = [s for s in stages if s.get("primary_followup_needed")]
    driven = [s for s in need if s.get("paid_followup_read") and s.get("followup_after_primary_turn")]
    reads = [s for s in stages if s.get("paid_followup_read")]
    premature = [s for s in reads if s.get("fallback_read_without_need")]
    return dict(stages=len(stages), opportunities=len(need), feedback_driven=len(driven),
                true_feedback_driven_rate=(len(driven) / len(need)) if need else None,
                paid_followup_reads=len(reads), premature_followup_reads=len(premature),
                premature_fallback_rate=(len(premature) / len(reads)) if reads else None,
                blocked=sum(s.get("deferred", 0) for s in stages))
