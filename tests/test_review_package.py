"""Package loaders reject stale proposal/report verification records."""

import json
from pathlib import Path

import pytest

from app.risk.history import sha256_file
from app.risk.review_package import review_package


def write(path,data): path.write_text(json.dumps(data),encoding="utf-8")


@pytest.fixture
def summary_path(tmp_path):
    rule = {"version":1,"name":"test","all":[{"field":"amount_cents","op":"gt","value":100}]}
    proposal_path = tmp_path / "proposal.json"
    write(proposal_path,{"status":"candidate_validated","rule":rule,"human_approved":False,"deployed":False})
    replay = tmp_path / "replay"; replay.mkdir()
    frozen = replay / "frozen-candidate.json"
    write(frozen,{"rule":rule,"proposal_sha256":sha256_file(proposal_path)})
    report = {"status":"development_replay_completed","data_kind":"synthetic","metrics":{"fixture":True},
        "final_test_evaluated":False,"frozen_candidate_sha256":sha256_file(frozen)}
    report_path = replay / "report.json"; write(report_path,report)
    summary = tmp_path / "summary.json"
    write(summary,{"status":"candidate_validated","artifact_sha256":{"proposal.json":sha256_file(proposal_path)},
        "replay":{"output_directory":str(replay),"metrics":report["metrics"]}})
    write(tmp_path / "proposal-verification.json",{"status":"passed","summary_sha256":sha256_file(summary),"replay_link_checked":True})
    write(replay / "sql-verification.json",{"status":"passed","report_sha256":sha256_file(report_path)})
    return summary


def test_verified_material_bound_into_package(summary_path):
    package = review_package(summary_path)
    assert package["proposal_summary_sha256"]==sha256_file(summary_path)
    assert package["rule"]["name"]=="test"


@pytest.mark.parametrize("artifact",["proposal","report","frozen","proposal-proof","sql-proof"])
def test_changed_material_or_stale_proof_rejected(summary_path,artifact):
    root = summary_path.parent
    paths = {"proposal":root / "proposal.json","report":root / "replay/report.json",
        "frozen":root / "replay/frozen-candidate.json","proposal-proof":root / "proposal-verification.json",
        "sql-proof":root / "replay/sql-verification.json"}
    path = paths[artifact]
    value = json.loads(path.read_text(encoding="utf-8"))
    if artifact=="proposal": value["rule"]["all"][0]["value"] = 200
    if artifact=="report": value["metrics"] = {"changed":True}
    if artifact=="frozen": value["proposal_sha256"] = "0"*64
    if artifact in {"proposal-proof","sql-proof"}: value["status"] = "failed"
    write(path,value)
    with pytest.raises(ValueError): review_package(summary_path)
