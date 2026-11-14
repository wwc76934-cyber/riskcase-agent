"""Cost-ladder variant of the 12 held-out cases for the conditional-fallback study (U3).

Credits per source: primary channel 2, corroborating source 2, fallback channel 6. The held-out cases carry
4 credits on the fallback and an access budget of 6, which cannot pay for a full sweep. Here the fallback costs
6 and the budget is 12, so an exhaustive fetch (10 credits) is affordable and the saving from not fetching is
measurable. Only cost fields and the budget change; statements, outcomes and references are the held-out ones.
"""
from copy import deepcopy

from app.evaluation.v4_heldout import candidates as heldout_candidates
from app.investigation.contracts import digest

COST = {"registered-callback": 2, "linked-record-archive": 2, "app-attestation": 6}
BUDGET = 12


def cost_variant(snapshot):
    snap = deepcopy(snapshot)
    for record in snap["records"]:
        if record.get("channel_ref") in COST:
            record["cost_units"] = COST[record["channel_ref"]]
    snap["access_budget_units"] = BUDGET
    snap["records_sha256"] = digest(snap["records"])
    snap["case_id"] = snap["case_id"].replace("v31-eval-", "v5cost-")
    snap["alert"] = dict(snap["alert"], alert_id=snap["case_id"] + "-alert")
    return snap


def candidates():
    for snapshot, reference in heldout_candidates():
        variant = cost_variant(snapshot)
        yield variant, dict(deepcopy(reference), case_id=variant["case_id"])


def full_sweep_credits(snapshot):
    return sum(r["cost_units"] for r in snapshot["records"])
