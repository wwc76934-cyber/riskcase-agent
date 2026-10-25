from contextlib import closing
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from app.risk.candidate_rules import FIELDS
from app.risk.capacity_experiment import materialize,evaluate
from app.risk.capacity_policy import POLICIES
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


@pytest.fixture
def experiment(tmp_path):
    features=tmp_path / "features.sqlite";labels=tmp_path / "labels.sqlite"
    identities=[f"tx{i}" for i in range(6)]
    stamps=[f"2018-01-0{1+i//3}T12:00:0{i}" for i in range(6)]
    with sqlite3.connect(features) as db:
        db.execute("CREATE TABLE features(transaction_id TEXT PRIMARY KEY,event_time TEXT,split TEXT,"+",".join(name+" INTEGER" for name in sorted(FIELDS))+")")
        for i,(identity,stamp) in enumerate(zip(identities,stamps)):
            f={key:0 for key in FIELDS};f.update(amount_cents=40000+i,history_30d_complete=1,has_history_30d=1,merchant_seen_30d=i%2)
            db.execute("INSERT INTO features VALUES ("+",".join("?" for _ in range(3+len(FIELDS)))+")",(identity,stamp,"validation",*[f[k] for k in sorted(FIELDS)]))
    with sqlite3.connect(labels) as db:
        db.execute("CREATE TABLE labels(transaction_id TEXT PRIMARY KEY,is_fraud INTEGER,split TEXT)")
        db.executemany("INSERT INTO labels VALUES (?,?,'validation')",[(identity,int(i in (0,3))) for i,identity in enumerate(identities)])
        db.execute("INSERT INTO labels VALUES ('test-never-used',1,'test')")
    scores=tmp_path / "scores.npz"
    np.savez(scores,transaction_id=np.array(identities),event_time=np.array(stamps),hist_gradient_boosting=np.array([.9,.8,.7,.6,.5,.4]))
    queue=tmp_path / "queue.jsonl"
    queue.write_text("\n".join(json.dumps({"strategy":"hist_gradient_boosting","transaction_id":identity}) for identity in identities),encoding="utf-8")
    directory=tmp_path / "experiment";directory.mkdir()
    protocol={"split":"validation","period":["2018-01-01","2019-01-01"],"code_file_sha256":{},
        "inputs":{key:{"path":str(path),"sha256":sha256_file(path)} for key,path in (("features",features),("scores",scores),("baseline_queue",queue))},
        "rule":{"version":1,"name":"fixture","all":[{"field":"merchant_seen_30d","op":"eq","value":0}]},
        "budgets":[5,10,20],"primary_budget":10,"policies":list(POLICIES),"tie_seed":"riskcase-review-ties-v1","expected_rows":6,"expected_positives":2,"expected_baseline_top10_tp":2,
        "labels_path":str(labels),"labels_sha256":sha256_file(labels),"data_kind":"synthetic_fixture","limits":["Fixture not source data."]}
    write_json(directory / "protocol.json",protocol)
    return tmp_path,directory,protocol


def test_plan_is_frozen_without_opening_label_database(experiment,monkeypatch):
    root,directory,protocol=experiment
    real_connect=sqlite3.connect
    def guarded(path,*args,**kwargs):
        if "labels.sqlite" in str(path): raise AssertionError("Labels entered planning")
        return real_connect(path,*args,**kwargs)
    monkeypatch.setattr(sqlite3,"connect",guarded)
    manifest=materialize(root,directory)
    assert manifest["labels_read"] is False and manifest["baseline_top10_matches_original"]
    assert manifest["rows"]==6 and manifest["days"]==2


def test_full_plan_then_evaluation_excludes_test_labels(experiment):
    root,directory,_=experiment
    materialize(root,directory)
    report=evaluate(root,directory)
    assert report["metrics"]["10/hgb"]["positives"]==2
    assert report["metrics"]["10/hgb"]["reviews"]==6 and report["metrics"]["10/hgb"]["capacity"]==20
    assert report["metrics"]["10/rule_only"]["reviews"]==3
    assert not report["human_approved"] and not report["deployed"] and not report["final_test_evaluated"]
    with pytest.raises(ValueError): materialize(root,directory)
    with pytest.raises(ValueError): evaluate(root,directory)


@pytest.mark.parametrize("artifact",["ranking-inputs.sqlite","planned-reviews.sqlite"])
def test_changed_selection_cannot_be_scored(experiment,artifact):
    root,directory,_=experiment
    materialize(root,directory)
    with open(directory / artifact,"ab") as handle: handle.write(b"changed")
    with pytest.raises(ValueError,match="Frozen plans changed"): evaluate(root,directory)
    assert not (directory / "report.json").exists()


def test_labels_cannot_be_scored_before_plan_freeze(experiment):
    root,directory,_=experiment
    with pytest.raises(FileNotFoundError): evaluate(root,directory)


def test_independent_source_sql_audit_rejects_invented_metrics(experiment):
    from scripts.verify_capacity_experiment import verify
    root,directory,_=experiment
    materialize(root,directory);evaluate(root,directory)
    proof=verify(root,directory)
    assert proof["source_rows_checked"]==6 and proof["ranking_lists_checked"]==12 and proof["monthly_checks"]==4
    report=json.loads((directory / "report.json").read_text())
    report["metrics"]["10/hgb"]["true_positives"]+=1
    write_json(directory / "report.json",report)
    with pytest.raises(ValueError,match="Paired metrics"): verify(root,directory)


@pytest.mark.parametrize("artifact",["report.json","plan-manifest.json","planned-reviews.sqlite"])
def test_review_package_rejects_changes_after_independent_verification(experiment,monkeypatch,artifact):
    import shutil
    import app.cli.capacity_experiment as cli
    from scripts.verify_capacity_experiment import verify
    root,directory,protocol=experiment
    base={"proposal_sha256":"fixture"}
    protocol.update(proposal_path=str(root / "proposal.json"),proposal_package=base)
    write_json(directory / "protocol.json",protocol)
    (root / "scripts").mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/verify_capacity_experiment.py",root / "scripts/verify_capacity_experiment.py")
    monkeypatch.setattr(cli,"review_package",lambda *_:base)
    materialize(root,directory);evaluate(root,directory);verify(root,directory)
    assert "capacity_experiment" in cli.capacity_package(root,directory)
    if artifact.endswith(".json"):
        body=json.loads((directory / artifact).read_text());body["tampered"]=True;write_json(directory / artifact,body)
    else:
        with open(directory / artifact,"ab") as handle: handle.write(b"tampered")
    with pytest.raises(ValueError): cli.capacity_package(root,directory)


def test_review_revision_is_pending_and_repeated_registration_is_idempotent(tmp_path,monkeypatch):
    import app.cli.capacity_experiment as cli
    from app.risk.review_store import ReviewStore
    directory=tmp_path / "experiment";directory.mkdir()
    (directory / "protocol.json").write_text("{}")
    (directory / "report.json").write_text("{}")
    store=ReviewStore(tmp_path / "runs/reviews.sqlite")
    original={"proposal_sha256":"same-proposal","rule":{"name":"same-rule"}}
    state=store.register("candidate",original,expected_version=0,actor="fixture",request_id="original")
    store.decide("candidate",original,expected_version=1,package_hash=state["package_hash"],decision="approve",actor="fixture-reviewer",note="Only fixture",request_id="fixture-approval")
    monkeypatch.setattr(cli,"load_protocol",lambda *_:{"original_review_candidate_id":"candidate"})
    monkeypatch.setattr(cli,"capacity_package",lambda *_:{**original,"capacity_experiment":{"report_sha256":"new-research-evidence"}})
    result=cli.register(tmp_path,directory)
    assert result["status"]["version"]==2 and result["status"]["effective_status"]=="pending_review"
    assert not result["status"]["approved_for_local_research"] and not result["status"]["deployed"]
    again=cli.register(tmp_path,directory)
    assert again["audit"]["events"]==result["audit"]["events"]
