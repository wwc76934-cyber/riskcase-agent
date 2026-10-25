"""Label-blind score alignment, real bound cases and review-only package integration."""

from datetime import datetime
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.cli.release_demo import TIE_SEED, load_label_free_development, prepare, rank_alerts, verify_package, verify_score_alignment
from app.risk.features import FeatureTransaction, iter_history_features
from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS
from app.risk.rule_baseline import DailyReviewer


class ToyPredictor:
    def __init__(self, seed): self.seed = seed
    def predict(self, frame): return np.asarray(.1 + frame.amount_cents.to_numpy()/100000 + (self.seed-42)*.01, dtype=np.float32)


class ToyEnsemble:
    def __init__(self, models, *, trusted_local):
        assert trusted_local
        self.predictors = [ToyPredictor(seed) for seed in (42,43,44)]
        self.metadata = {"kind": "TEST_ONLY_ANALYTIC_SCORER_NOT_TRAINED_MODEL", "model_count": 3,
                         "model_artifacts_verified": False, "members": models}
    def score_many(self, rows):
        scores = [p.predict(pd.DataFrame(rows)) for p in self.predictors]
        return [[float(score[i]) for score in scores] for i in range(len(rows))]


@pytest.fixture
def inputs(tmp_path):
    cohort = tmp_path / "cohort"; (cohort / "dataset").mkdir(parents=True)
    source = cohort / "dataset/facts.sqlite"
    records = []
    for i in range(4):
        user = "u" + str(i)
        records += [(user+"-old",i*10,user,"c","2017-12-25T11:00:00",100,"A","Online","5411","warmup"),
                    (user+"-recent",i*10+1,user,"c","2017-12-31T11:00:00",300,"B","Online","5411","warmup"),
                    (user+"-target",i*10+2,user,"c",f"2018-01-20T1{i}:00:00",10000+i*1000,"Z","Online","5411","validation"),
                    (user+"-future",i*10+3,user,"c","2019-01-20T12:00:00",90000,"Z","Online","5411","test")]
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE transactions(transaction_id TEXT PRIMARY KEY,source_row INTEGER,user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)")
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", records)
    report = cohort / "build-report.json"
    report.write_text(json.dumps({"artifact_sha256": {"dataset/facts.sqlite": sha256_file(source)}, "source_csv_fully_scanned": True,
                                 "coverage": {"warmup_start": "2017-12-01T00:00:00", "end": "2020-01-01T00:00:00"}}))
    features = tmp_path / "features"; features.mkdir()
    transactions = [FeatureTransaction(r[0],r[2],datetime.fromisoformat(r[4]),r[5],r[6],r[7],r[8],r[9]) for r in sorted(records, key=lambda r:(r[2],r[4]))]
    rows = list(iter_history_features(transactions, coverage_start=datetime(2017,12,1)))
    names = ("transaction_id","user_id","event_time","split","history_30d_complete", *MODEL_COLUMNS)
    feature_db = features / "features.sqlite"
    with sqlite3.connect(feature_db) as db:
        db.execute("CREATE TABLE features(" + ",".join(c+(" TEXT" if c in ("transaction_id","user_id","event_time","split","mcc","channel") else " INTEGER") for c in names) + ")")
        db.executemany("INSERT INTO features VALUES (" + ",".join("?" for _ in names) + ")", [tuple(row[n] for n in names) for row in rows])
    feature_manifest = features / "manifest.json"
    feature_manifest.write_text(json.dumps({"status":"features_built","labels_read":False,"facts_sha256":sha256_file(source),
                                           "cohort_report_sha256":sha256_file(report),"features_sha256":sha256_file(feature_db)}))
    run = tmp_path / "neural-run"; run.mkdir()
    ordered = sorted([r for r in rows if r["split"] == "validation"], key=lambda r:r["transaction_id"])
    frame = pd.DataFrame(ordered)
    scores = []
    for seed in (42,43,44):
        directory = run / f"confirmation-seed-{seed}"; (directory / "model").mkdir(parents=True)
        for name in ("model.pt", "preprocessor.joblib"): (directory / "model" / name).write_text(str(seed)+name)
        score = ToyPredictor(seed).predict(frame); scores.append(score)
        np.save(directory / "development-scores.npy", score)
    np.save(run / "ensemble-development-scores.npy", np.mean(scores,axis=0))
    (run / "data.json").write_text(json.dumps({"manifest_sha256":sha256_file(feature_manifest),"features_sha256":sha256_file(feature_db),
                                             "labels_sha256":"NO-LABEL-DATABASE-EXISTS-IN-THIS-TEST"}))
    body = {"status":"neural_v2_development_complete","final_test_read":False,"development_rows":4,
            "selected":{"seeds":[42,43,44]},"seeds":[{"seed":s} for s in (42,43,44)],
            "artifact_sha256":{str(p.relative_to(run)).replace("\\","/"):sha256_file(p) for p in run.rglob("*") if p.is_file()}}
    (run / "report.json").write_text(json.dumps(body))
    return run, feature_manifest, report


def test_only_2018_label_free_rows_loaded_in_training_order(inputs):
    run, features, cohort = inputs
    X, meta, seed_scores, ensemble, proof = load_label_free_development(run, features, cohort)
    assert len(X) == len(meta) == 4 and meta.transaction_id.tolist() == sorted(meta.transaction_id)
    assert meta.event_time.str.startswith("2018").all() and not proof["labels_read"]
    assert np.array_equal(ensemble,np.mean(seed_scores,axis=0))
    checked = verify_score_alignment(ToyEnsemble([],trusted_local=True),X,seed_scores)
    assert checked["max_absolute_differences"] == [0.,0.,0.]


def test_daily_ranking_matches_existing_label_independent_tie_policy():
    metadata = pd.DataFrame({"transaction_id":["id"+str(i) for i in range(24)],"user_id":["u"+str(i) for i in range(24)],
                             "event_time":"2018-06-01T12:00:00","split":"validation"})
    scores = np.asarray([.5]*24)
    queue = rank_alerts(metadata,scores)
    reviewer = DailyReviewer(10,TIE_SEED)
    for i, row in metadata.iterrows(): reviewer.add(transaction_id=row.transaction_id,event_time=row.event_time,score=float(scores[i]),is_fraud=False,reasons=[],threshold_alert=None)
    _, expected = reviewer.finish()
    assert [r["transaction_id"] for r in queue] == [r["transaction_id"] for r in expected]
    assert [r["day_rank"] for r in queue] == list(range(1,11))


def test_permuted_scores_fail_reprediction_even_with_valid_shape(inputs):
    run, features, cohort = inputs
    X, _, seeds, _, _ = load_label_free_development(run,features,cohort)
    with pytest.raises(ValueError,match="order_or_model"):
        verify_score_alignment(ToyEnsemble([],trusted_local=True),X,[scores[::-1] for scores in seeds])


def test_seed_mean_and_artifact_hash_are_both_enforced(inputs):
    run, features, cohort = inputs
    path = run / "ensemble-development-scores.npy"
    np.save(path,np.zeros(4))
    with pytest.raises(ValueError,match="artifact_changed"): load_label_free_development(run,features,cohort)
    report = json.loads((run / "report.json").read_text()); report["artifact_sha256"][path.name]=sha256_file(path); (run / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError,match="mean_differs"): load_label_free_development(run,features,cohort)


def test_required_score_hash_cannot_be_silently_omitted(inputs):
    run, features, cohort = inputs
    report=json.loads((run/"report.json").read_text())
    report["artifact_sha256"].pop("confirmation-seed-43/development-scores.npy")
    (run/"report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError,match="unhashed"): load_label_free_development(run,features,cohort)


def test_end_to_end_neural_cases_and_review_package(inputs,tmp_path):
    run, features, cohort = inputs
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "prepared"
    result = prepare(root,output,neural_run=run,features_manifest=features,cohort_report=cohort,users=2,scorer_factory=ToyEnsemble)
    assert result["representative_users"] == 2 and result["real_LLM_requests"] == 0 and not result["labels_read"]
    assert len({c["user_id"] for c in result["cases"]}) == 2
    assert all(c["state"] == "awaiting_review" and c["scope_confirmation"] == "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW" for c in result["cases"])
    assert all(c["sql_verification"]["task_passed"] for c in result["cases"])
    for entry in result["cases"]:
        manifest=json.loads((output/entry["manifest"]).read_text())
        assert manifest["alert"]["detector"] == "neural_v2_ensemble" and "not_probability" in manifest["alert"]["score_kind"]
        assert entry["recomputed_score_absolute_difference"] <= 1e-6
        assert not entry["policy_approved"] and not entry["deployed"]
    token = (output/"service/auth.token").read_text()
    assert token not in (output/"README.md").read_text() and token not in (output/"manifest.json").read_text()
    assert "service/jobs.sqlite" not in result["artifact_sha256"] and "service/jobs.sqlite" in result["mutable_runtime_snapshot_sha256"]
    assert verify_package(output,include_runtime_snapshot=True)["status"] == "prepared_material_hashes_verified"
    with pytest.raises(FileExistsError): prepare(root,output,neural_run=run,features_manifest=features,cohort_report=cohort,users=2,scorer_factory=ToyEnsemble)
    # Human UI usage is allowed to mutate the live job database, not the prepared reports.
    with sqlite3.connect(output/"service/jobs.sqlite") as db: db.execute("UPDATE jobs SET updated=updated+1")
    assert verify_package(output)["runtime_snapshot_checked"] is False
    with pytest.raises(ValueError,match="artifact_changed"): verify_package(output,include_runtime_snapshot=True)


def test_existing_output_and_out_of_period_rank_rejected(inputs,tmp_path):
    run, features, cohort = inputs
    output=tmp_path/"exists"; output.mkdir(); (output/"mine.txt").write_text("preserve")
    with pytest.raises(FileExistsError): prepare(Path(__file__).resolve().parents[1],output,neural_run=run,features_manifest=features,cohort_report=cohort,scorer_factory=ToyEnsemble)
    assert (output/"mine.txt").read_text() == "preserve"
    metadata=pd.DataFrame({"transaction_id":["future"],"user_id":["u"],"event_time":["2019-01-01T00:00:00"],"split":["test"]})
    with pytest.raises(ValueError,match="only_2018"): rank_alerts(metadata,np.asarray([.5]))
