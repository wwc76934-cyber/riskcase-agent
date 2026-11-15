"""B5: semantic-guided adaptive investigation workflow.

1. Free evidence and prerequisites are read as in B0.
2. A semantic probe looks at the customer's statement (read once at the cheapest paid customer source if no
   statement is acquired yet). Plain wording routes to the deterministic B0 report, with no model call for
   the report. Pragmatically complex wording routes to the model report.
3. When the probe flags a complex statement, evidence is read in a different order: corroborating originals
   (merchant records) before dearer alternative channels. B4 and B2 had spent the budget on the alternative
   channel and missed the corroborating record in the late-retraction and friendly-fraud cases.
"""
from app.investigation.v3.rule_workflow import (acquired_ids, checked_call, collect_free_and_prerequisites, compile_ledger,
                                                expiry_key, originals, route_score)


def customer_text(ledger):
    parts = []
    for lines in originals(ledger):
        if lines[0][1]["source_type"] == "customer":
            parts.append("\n".join(line["text"] for _, line in lines))
    return "\n".join(parts)


def _affordable(catalog, s):
    return s["cost_units"] <= catalog["remaining_cost_units"] and s["latency_minutes"] <= catalog["remaining_minutes"]


def collect_b5(source, probe_fn):
    context = collect_free_and_prerequisites(source)
    catalog = context["catalog"]
    ledger = compile_ledger(source, context)
    text = customer_text(ledger)
    probe_reads = []
    if not text:
        known = acquired_ids(source)
        candidates = [s for s in catalog["sources"] if s["source_id"] not in known and s["source_type"] == "customer"
                      and set(s["requires"]) <= known and s.get("readable", True) and _affordable(catalog, s)]
        if candidates:
            pick = min(candidates, key=lambda s: (s["cost_units"], s["latency_minutes"], s["source_id"]))
            catalog = checked_call(source, "v3_read", dict(source_ids=[pick["source_id"]]))["catalog"]
            probe_reads.append(pick["source_id"])
            ledger = compile_ledger(source, dict(frame=context["frame"], catalog=catalog))
            text = customer_text(ledger)
    verdict = probe_fn(text) if text else dict(complex=False, flags=[], used_model=False, cny="0")
    flagged = bool(verdict["complex"])
    pending = [s for s in catalog["sources"] if s["source_id"] not in acquired_ids(source)]
    pending.sort(key=lambda s: ((0 if (flagged and s["source_type"] == "merchant") else 1),) +
                 (expiry_key(s), -route_score(s["channel_ref"], ledger), -s["version"], s["cost_units"], s["latency_minutes"], s["source_id"]))
    reads = list(probe_reads)
    for item in pending:
        if "query_clock" in catalog and not any(s["source_id"] == item["source_id"] and s["readable"] for s in catalog["sources"]):
            continue
        if not set(item["requires"]) <= acquired_ids(source) or not _affordable(catalog, item):
            continue
        catalog = checked_call(source, "v3_read", dict(source_ids=[item["source_id"]]))["catalog"]
        reads.append(item["source_id"])
    ledger = compile_ledger(source, dict(frame=context["frame"], catalog=catalog))
    plan = dict(mode="b5", route="LLM" if flagged else "B0", probe=verdict, source_reads=reads, model_requests=0)
    return ledger, plan
