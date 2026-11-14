import json

from app.evaluation.v31_query_benchmark import candidates
from app.investigation.v3.native_planner import NativePlanner
from tests.test_v3_source_access import start


class Greedy:
    """Always requests every currently feasible source, as a latency-minimising model would."""
    simulation = True

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls > 3:
            return dict(role="assistant", content="Ready to report")
        ids = tools[0]["function"]["parameters"]["properties"]["source_ids"]["items"]["enum"]
        args = dict(source_ids=ids, purpose="Read everything available", hypothesis_to_test="Gather evidence")
        return dict(role="assistant", content=None, tool_calls=[
            dict(id=f"call-{self.calls}", type="function", function=dict(name="v3_read", arguments=json.dumps(args)))])

    def snapshot(self):
        return dict(simulated=True, requests=self.calls, estimated_cny="0")


def case_with_two_paid_tiers():
    for snapshot, _ in candidates():
        costs = {r["cost_units"] for r in snapshot["records"] if r["cost_units"] > 0}
        if {2, 4} <= costs:
            return snapshot
    raise AssertionError("no two-tier case")


def run(tmp_path, gate):
    _, _, source = start(tmp_path, case_with_two_paid_tiers())
    source.advance(1)
    source.advance(2)
    result = NativePlanner(source, 2, Greedy(), tier_gate=gate).run()
    return result, source


def test_without_gate_a_greedy_model_reads_both_paid_tiers_at_once(tmp_path):
    result, _ = run(tmp_path, False)
    assert not any("deferred_source_ids" in row for row in result["trace"])
    assert any(len(row["source_ids"]) >= 2 for row in result["trace"])


def test_with_gate_dearer_tier_waits_for_a_later_turn(tmp_path):
    result, source = run(tmp_path, True)
    deferred = [row for row in result["trace"] if row.get("deferred_source_ids")]
    assert deferred, "greedy batch should have been split"
    first = deferred[0]
    assert first["ok"] and not set(first["deferred_source_ids"]) & set(first["source_ids"])
    later = [row for row in result["trace"] if row["model_turn"] > first["model_turn"] and row["ok"]]
    assert later and set(first["deferred_source_ids"]) & set(later[0]["source_ids"]), "deferred source read next turn"
