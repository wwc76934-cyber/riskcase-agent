"""Cost-tier sequencing gate for dynamic source reads.

Models trained for low latency batch every tool they might need into one call,
which removes any chance to react to what the first read returned. This gate
leaves free reads (cost 0) unrestricted. Among paid reads in one call, only
the cheapest tier runs; dearer sources are deferred without cost or state
change and the model must choose again after seeing the cheaper result.

The host never chooses WHICH sources to read, only the order in which paid
tiers become available. A deferred source is not an error and is reported back
as a structured receipt so the model can re-request it next turn.
"""

DEFERRED = "DEFERRED_BY_HOST_POLICY"
REASON = ("A cheaper paid source in this call must be observed before a dearer one. "
          "Inspect its result first and request this source again if still needed.")


def tier_of(cost_units):
    """0 free, 1 internal (cost 1), 2 primary (cost 2), 3 fallback or deep (cost 3 or more)."""
    if cost_units <= 0:
        return 0
    return min(int(cost_units), 3)


def split_batch(requested, catalog_sources, observed):
    """Return ``(allowed, deferred)`` for a requested id list. Order is preserved."""
    cost = {s["source_id"]: s["cost_units"] for s in catalog_sources}
    fresh = [k for k in requested if k not in observed]
    paid_tiers = sorted({tier_of(cost.get(k, 0)) for k in fresh if tier_of(cost.get(k, 0)) > 0})
    if len(paid_tiers) <= 1:
        return list(requested), []
    cheapest = paid_tiers[0]
    allowed = [k for k in requested if k in observed or tier_of(cost.get(k, 0)) in (0, cheapest)]
    deferred = [k for k in requested if k not in allowed]
    return allowed, deferred


def deferral_receipts(deferred, catalog_sources):
    cost = {s["source_id"]: s["cost_units"] for s in catalog_sources}
    return [dict(source_id=k, status=DEFERRED, tier=tier_of(cost.get(k, 0)), cost_charged=0, reason=REASON)
            for k in deferred]


def planning_metrics(trace, catalog_sources):
    """Derived from a planner trace. ``trace`` rows come from NativePlanner.

    * credits: total cost of sources actually read
    * premature_blocked: sources deferred by this gate
    * feedback_driven_turns: turns whose source set was chosen after at least one earlier read
    """
    cost = {s["source_id"]: s["cost_units"] for s in catalog_sources}
    read, credits, blocked, driven, seen_turns = set(), 0, 0, 0, set()
    for row in trace:
        blocked += len(row.get("deferred_source_ids", []))
        if not row.get("ok"):
            continue
        fresh = [k for k in row.get("source_ids", []) if k not in read]
        credits += sum(cost.get(k, 0) for k in fresh)
        if read and fresh and row.get("model_turn") not in seen_turns:
            driven += 1
        seen_turns.add(row.get("model_turn"))
        read.update(row.get("source_ids", []))
    return dict(credits=credits, premature_blocked=blocked, feedback_driven_turns=driven,
                sources_read=len(read))
