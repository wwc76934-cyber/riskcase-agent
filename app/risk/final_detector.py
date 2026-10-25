"""One frozen 2019 detector evaluation: predictions/queues first, outcomes second."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import time

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS
from app.risk.source_merchant_pack import write_json


CODE = ["app/risk/final_detector.py", "app/cli/final_detector.py", "app/risk/neural_v2.py",
        "app/risk/neural_model.py", "app/risk/ml_models.py", "app/risk/features.py",
        "app/risk/detector_v2_controls.py"]
TIE_SEED = "riskcase-review-ties-v1"


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name): raise ValueError("simple_model_name_required")
    return name


def freeze(root, directory, recipes, features_manifest):
    """Copies trusted model artifacts before any final-period features/labels query."""
    root, directory, features_manifest = map(Path, (root, directory, features_manifest))
    manifest = json.loads(features_manifest.read_text(encoding="utf-8"))
    cohort_path = Path(manifest["cohort_report"])
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    features, labels = features_manifest.parent / "features.sqlite", cohort_path.parent / "dataset/evaluation-labels.sqlite"
    if sha256_file(cohort_path) != manifest["cohort_report_sha256"] or sha256_file(features) != manifest["features_sha256"] or sha256_file(labels) != cohort["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
        raise ValueError("frozen_data_changed")
    models = recipes["models"]
    names = [safe_name(m["name"]) for m in models]
    if len(set(names)) != len(names) or not models or recipes["primary_model"] not in names: raise ValueError("distinct_primary_and_baseline_models_required")
    baselines = recipes.get("comparison_baselines", [n for n in ("hgb_v1", "hgb_v2") if n in names] or [names[0]])
    if not baselines or len(set(baselines)) != len(baselines) or not set(baselines) <= set(names): raise ValueError("explicit_comparison_baselines_required")
    if recipes["training_period"] != "2016-01-01/2018-01-01" or recipes["selection_period"] != "2018": raise ValueError("declared_training_and_development_periods_required")
    if any(m["kind"] not in ("sklearn", "mlp_v1", "neural_v2", "amount") for m in models): raise ValueError("unsupported_model")
    directory.mkdir(parents=True, exist_ok=False)
    snapshots = []
    for model in models:
        copied = {k: v for k, v in model.items() if k != "artifacts"}
        artifacts = {}
        for name, source in model.get("artifacts", {}).items():
            parts = name.split("/")
            if not 1 <= len(parts) <= 2 or any(p in (".", "..") or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", p) for p in parts): raise ValueError("simple_artifact_name_required")
            source = Path(source)
            if not source.is_file(): raise ValueError("model_artifact_missing")
            target = directory / "models" / model["name"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            artifacts[name] = {"path": str(target.relative_to(directory)).replace("\\", "/"), "sha256": sha256_file(target)}
        snapshots.append(copied | {"artifacts": artifacts})
    contract = {"version": 1, "frozen_at": datetime.now(timezone.utc).isoformat(), "data_kind": "IBM_TabFormer_synthetic",
                "training_period": recipes["training_period"], "selection_period": "2018", "evaluation_period": "2019",
                "primary_model": recipes["primary_model"], "selection_rationale": recipes["selection_rationale"], "models": snapshots,
                "comparison_baselines": baselines,
                "features_manifest": str(features_manifest.resolve()), "features_manifest_sha256": sha256_file(features_manifest),
                "features": str(features.resolve()), "features_sha256": sha256_file(features),
                "cohort_report": str(cohort_path.resolve()), "cohort_report_sha256": sha256_file(cohort_path),
                "labels": str(labels.resolve()), "labels_sha256": sha256_file(labels),
                "expected_rows": manifest["rows_by_split"]["test"],
                "daily_budgets": [5, 10, 20], "primary_budget": 10, "tie_seed": TIE_SEED,
                "bootstrap": {"unit": "day", "repetitions": 2000, "seed": 20261022},
                "code_file_sha256": {name: sha256_file(root / name) for name in CODE},
                "limits": ["Synthetic retrospective outcomes, not real-world fraud effectiveness.",
                           "Same cohort users appear across periods; this is time holdout, not user holdout.",
                           "2018 was repeatedly used for development; 2019 does not select checkpoints, seeds, architecture or strategies.",
                           "Partition aggregate counts were previously inventoried; final model rankings/outcomes were not evaluated before freeze.",
                           "End-of-day review capacity, not real-time authorization or operational loss reduction.",
                           "Unknown label confirmation/ingestion timing; no delayed-outcome deployment simulation.",
                           "Day bootstrap sensitivity ignores longer temporal/user correlation; not a causal confidence claim.",
                           "Weighted scores are not calibrated real-world probabilities."]}
    write_json(directory / "contract.json", contract)
    return contract


def load(root, directory, *, check_code=True):
    root, directory = Path(root), Path(directory)
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    for model in contract["models"]:
        for artifact in model["artifacts"].values():
            path = (directory / artifact["path"]).resolve()
            if not path.is_relative_to(directory.resolve()) or sha256_file(path) != artifact["sha256"]: raise ValueError("frozen_model_changed")
    for path_key, hash_key in (("features_manifest", "features_manifest_sha256"), ("features", "features_sha256"), ("cohort_report", "cohort_report_sha256")):
        if sha256_file(Path(contract[path_key])) != contract[hash_key]: raise ValueError("frozen_data_changed")
    if check_code:
        for name, expected in contract["code_file_sha256"].items():
            if sha256_file(root / name) != expected: raise ValueError("evaluation_code_changed")
    return contract


def predict_model(model, directory, frame):
    """Only artifacts produced locally then bound by freeze() may reach this function."""
    import joblib
    import torch
    artifacts = {name: directory / row["path"] for name, row in model["artifacts"].items()}
    if model["kind"] == "amount": return np.maximum(0, frame["amount_cents"].to_numpy(dtype=float))
    if model["kind"] == "sklearn": return joblib.load(artifacts["model.joblib"]).predict_proba(frame[list(MODEL_COLUMNS)])[:, 1]
    if model["kind"] == "mlp_v1":
        from app.risk.neural_model import RiskMLP, dense_float32, make_loader, predict_batches
        prep = joblib.load(artifacts["preprocessor.joblib"])
        architecture = json.loads(artifacts["architecture.json"].read_text(encoding="utf-8"))
        network = RiskMLP(architecture["refit_input_width"], architecture["hidden_widths"], architecture["dropout"])
        network.load_state_dict(torch.load(artifacts["model.pt"], map_location="cpu", weights_only=True))
        return predict_batches(network, make_loader(dense_float32(prep.transform(frame[list(MODEL_COLUMNS)])), batch_size=16384))[0]
    from app.risk.neural_v2 import predict_v2
    members = model.get("members", [""])
    predictions = []
    for member in members:
        # Each bundle must have model.pt and preprocessor.joblib in the same bound subdirectory.
        name = member + "model.pt"
        if name not in artifacts: raise ValueError("neural_bundle_missing")
        predictions.append(predict_v2(artifacts[name].parent, frame[list(MODEL_COLUMNS)]))
    return np.mean(predictions, axis=0, dtype=np.float64)


def freeze_predictions(root, directory, *, predictor=predict_model):
    directory = Path(directory)
    contract = load(root, directory)
    if (directory / "predictions.json").exists(): raise ValueError("predictions_already_frozen")
    if (directory / "outcomes-opened.json").exists(): raise ValueError("final_outcomes_already_opened")
    database = directory / "predictions.sqlite"
    if database.exists(): raise ValueError("partial_prediction_run_requires_inspection")
    # No outcome database is opened/attached during feature read, inference or review selection.
    with closing(sqlite3.connect(Path(contract["features"]).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        columns = ",".join(dict.fromkeys(("transaction_id", "user_id", "event_time", "split", "history_30d_complete", *MODEL_COLUMNS)))
        frame = pd.read_sql_query(f"SELECT {columns} FROM features WHERE split='test' AND event_time>='2019-01-01' AND event_time<'2020-01-01' ORDER BY transaction_id", db)
    if len(frame) != contract["expected_rows"] or not len(frame) or frame["transaction_id"].duplicated().any() or not frame["history_30d_complete"].eq(1).all(): raise ValueError("final_feature_contract_failed")
    ties = [hashlib.sha256((TIE_SEED + "\0" + identity).encode()).hexdigest() for identity in frame["transaction_id"]]
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE facts(transaction_id TEXT PRIMARY KEY,user_id TEXT,event_time TEXT,channel TEXT,amount_cents INTEGER,tie TEXT); CREATE TABLE scores(model TEXT,transaction_id TEXT,score REAL,PRIMARY KEY(model,transaction_id)); CREATE TABLE reviews(model TEXT,budget INTEGER,transaction_id TEXT,day TEXT,day_rank INTEGER,PRIMARY KEY(model,budget,transaction_id));")
        db.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?)", zip(frame["transaction_id"], frame["user_id"], frame["event_time"], frame["channel"], frame["amount_cents"], ties))
        inference = {}
        from threadpoolctl import threadpool_limits
        import torch
        torch.set_num_threads(2)
        with threadpool_limits(limits=2):
            for model in contract["models"]:
                begin = time.monotonic()
                values = np.asarray(predictor(model, directory, frame), dtype=np.float64)
                if values.shape != (len(frame),) or not np.isfinite(values).all() or (model["kind"] != "amount" and ((values < 0) | (values > 1)).any()): raise ValueError("invalid_final_predictions")
                db.executemany("INSERT INTO scores VALUES (?,?,?)", ((model["name"], identity, float(value)) for identity, value in zip(frame["transaction_id"], values)))
                for budget in contract["daily_budgets"]:
                    db.execute("INSERT INTO reviews SELECT model,?,transaction_id,day,day_rank FROM (SELECT s.model,s.transaction_id,substr(f.event_time,1,10) day,ROW_NUMBER() OVER (PARTITION BY substr(f.event_time,1,10) ORDER BY s.score DESC,f.tie DESC,s.transaction_id DESC) day_rank FROM scores s JOIN facts f USING(transaction_id) WHERE s.model=?) WHERE day_rank<=?", (budget, model["name"], budget))
                inference[model["name"]] = {"rows": len(values), "seconds": round(time.monotonic()-begin, 3)}
    manifest = {"status": "predictions_and_review_lists_frozen_before_outcomes", "labels_opened": False,
                "contract_sha256": sha256_file(directory / "contract.json"), "predictions_sha256": sha256_file(database), "inference": inference}
    write_json(directory / "predictions.json", manifest)
    return manifest


def independent_ap(y, scores):
    y, scores = np.asarray(y, dtype=int), np.asarray(scores, dtype=float)
    if not y.sum(): return None
    order = np.argsort(-scores, kind="stable")
    ordered, ranked = y[order], scores[order]
    ends = np.r_[np.flatnonzero(ranked[:-1] != ranked[1:]), len(y)-1]
    tp = np.cumsum(ordered)[ends]
    return float(np.sum(np.diff(np.r_[0, tp]) / y.sum() * (tp / (ends+1))))


def _counts(rows, positives, reviews, tp):
    return {"rows": int(rows), "positives": int(positives), "reviews": int(reviews), "true_positives": int(tp),
            "false_positives": int(reviews-tp), "false_negatives": int(positives-tp),
            "precision": float(tp/reviews) if reviews else None, "recall": float(tp/positives) if positives else None}


def evaluate(root, directory):
    directory = Path(directory)
    contract = load(root, directory)
    predictions = json.loads((directory / "predictions.json").read_text(encoding="utf-8"))
    if predictions["labels_opened"] or predictions["contract_sha256"] != sha256_file(directory / "contract.json") or predictions["predictions_sha256"] != sha256_file(directory / "predictions.sqlite"): raise ValueError("predictions_not_frozen")
    if sha256_file(Path(contract["labels"])) != contract["labels_sha256"]: raise ValueError("outcomes_changed")
    with (directory / "outcomes-opened.json").open("x", encoding="utf-8") as stream:
        json.dump({"opened_at": datetime.now(timezone.utc).isoformat(), "predictions_manifest_sha256": sha256_file(directory / "predictions.json"), "no_further_selection": True}, stream)
    result = audit(directory, contract=contract)
    write_json(directory / "report.json", result)
    return result


def audit(directory, *, contract=None):
    """Independent SQL membership checks + separate AP math, reproducible after opening."""
    directory = Path(directory)
    saved_contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    if contract is not None and contract != saved_contract: raise ValueError("external_contract_differs_from_frozen_contract")
    contract = saved_contract
    predictions = json.loads((directory / "predictions.json").read_text(encoding="utf-8"))
    if sha256_file(directory / "predictions.sqlite") != predictions["predictions_sha256"] or sha256_file(directory / "contract.json") != predictions["contract_sha256"]:
        raise ValueError("final_artifact_changed")
    if not (directory / "outcomes-opened.json").exists(): raise ValueError("outcomes_not_opened")
    opened = json.loads((directory / "outcomes-opened.json").read_text(encoding="utf-8"))
    if opened.get("predictions_manifest_sha256") != sha256_file(directory / "predictions.json") or opened.get("no_further_selection") is not True:
        raise ValueError("outcome_opening_binding_differs")
    if sha256_file(Path(contract["features"])) != contract["features_sha256"]: raise ValueError("frozen_features_changed")
    if sha256_file(Path(contract["labels"])) != contract["labels_sha256"]: raise ValueError("outcomes_changed")
    with closing(sqlite3.connect((directory / "predictions.sqlite").resolve().as_uri()+"?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("ATTACH DATABASE ? AS sourcefeatures", (Path(contract["features"]).resolve().as_uri()+"?mode=ro",))
        source_rows = db.execute("SELECT COUNT(*) FROM sourcefeatures.features WHERE split='test' AND event_time>='2019-01-01' AND event_time<'2020-01-01'").fetchone()[0]
        mismatches = db.execute("SELECT COUNT(*) FROM facts p LEFT JOIN sourcefeatures.features f ON p.transaction_id=f.transaction_id AND f.split='test' AND f.event_time>='2019-01-01' AND f.event_time<'2020-01-01' WHERE f.transaction_id IS NULL OR p.user_id IS NOT f.user_id OR p.event_time IS NOT f.event_time OR p.channel IS NOT f.channel OR p.amount_cents IS NOT f.amount_cents").fetchone()[0]
        projected = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        if source_rows != contract["expected_rows"] or projected != source_rows or mismatches: raise ValueError("final_source_projection_differs")
        for identity, tie in db.execute("SELECT transaction_id,tie FROM facts"):
            if tie != hashlib.sha256((contract["tie_seed"] + "\0" + identity).encode()).hexdigest(): raise ValueError("frozen_tie_differs")
        if {r[0] for r in db.execute("SELECT DISTINCT model FROM scores")} != {m["name"] for m in contract["models"]}: raise ValueError("score_models_differ")
        buckets = {(r[0], r[1]) for r in db.execute("SELECT DISTINCT model,budget FROM reviews")}
        if buckets != {(m["name"], budget) for m in contract["models"] for budget in contract["daily_budgets"]}: raise ValueError("review_buckets_differ")
        db.execute("ATTACH DATABASE ? AS outcomes", (Path(contract["labels"]).resolve().as_uri()+"?mode=ro",))
        actual = db.execute("SELECT COUNT(*),COUNT(DISTINCT f.transaction_id),SUM(l.is_fraud),SUM(CASE WHEN typeof(l.is_fraud)!='integer' OR l.is_fraud NOT IN (0,1) THEN 1 ELSE 0 END) FROM facts f JOIN outcomes.labels l USING(transaction_id) WHERE l.split='test'").fetchone()
        if actual[0] != contract["expected_rows"] or actual[1] != actual[0] or actual[3] != 0: raise ValueError("outcome_join_failed")
        rows, _, positives, _ = actual
        strategies, daily_counts = {}, {}
        for model in contract["models"]:
            name = model["name"]
            absent = db.execute("SELECT COUNT(*) FROM facts f LEFT JOIN scores s ON f.transaction_id=s.transaction_id AND s.model=? WHERE s.transaction_id IS NULL", (name,)).fetchone()[0]
            excess = db.execute("SELECT COUNT(*) FROM scores s LEFT JOIN facts f USING(transaction_id) WHERE s.model=? AND f.transaction_id IS NULL", (name,)).fetchone()[0]
            if absent or excess: raise ValueError("score_population_differs")
            values = db.execute("SELECT l.is_fraud,s.score FROM scores s JOIN outcomes.labels l USING(transaction_id) WHERE s.model=? AND l.split='test' ORDER BY s.transaction_id", (name,)).fetchall()
            if len(values) != rows: raise ValueError("score_population_differs")
            y, scores = np.asarray(values, dtype=np.float64).T
            if not np.isfinite(scores).all(): raise ValueError("invalid_final_predictions")
            ap, reference = independent_ap(y, scores), float(average_precision_score(y, scores)) if positives else None
            if ap is not None and abs(ap-reference) > 1e-12: raise ValueError("independent_ap_disagrees")
            per_budget = {}
            for budget in contract["daily_budgets"]:
                selected_rows = {r[0]: (r[1], r[2]) for r in db.execute("SELECT transaction_id,day,day_rank FROM reviews WHERE model=? AND budget=?", (name, budget))}
                selected = set(selected_rows)
                records = list(db.execute("SELECT s.transaction_id,s.score,f.tie,substr(f.event_time,1,10) day FROM scores s JOIN facts f USING(transaction_id) WHERE s.model=?", (name,)))
                grouped = {}
                for record in records: grouped.setdefault(record["day"], []).append(record)
                expected = {r["transaction_id"]: (day, rank) for day, items in grouped.items() for rank, r in enumerate(sorted(items, key=lambda r: (r["score"], r["tie"], r["transaction_id"]), reverse=True)[:budget], 1)}
                if selected_rows != expected: raise ValueError("independent_review_membership_or_rank_differs")
                tp = db.execute("SELECT COALESCE(SUM(l.is_fraud),0) FROM reviews r JOIN outcomes.labels l USING(transaction_id) WHERE r.model=? AND r.budget=? AND l.split='test'", (name, budget)).fetchone()[0]
                groups = {}
                for column, expr in (("month", "substr(f.event_time,1,7)"), ("channel", "f.channel")):
                    rows_group = db.execute(f"SELECT {expr} group_name,COUNT(*) rows,SUM(l.is_fraud) positives,SUM(CASE WHEN r.transaction_id IS NOT NULL THEN 1 ELSE 0 END) reviews,SUM(CASE WHEN r.transaction_id IS NOT NULL THEN l.is_fraud ELSE 0 END) tp FROM facts f JOIN outcomes.labels l USING(transaction_id) LEFT JOIN reviews r ON f.transaction_id=r.transaction_id AND r.model=? AND r.budget=? WHERE l.split='test' GROUP BY {expr}", (name, budget)).fetchall()
                    groups[column] = {r["group_name"]: _counts(r["rows"], r["positives"], r["reviews"], r["tp"]) for r in rows_group}
                per_budget[str(budget)] = _counts(rows, positives, len(selected), tp) | {"groups": groups, "observed_days": len(grouped), "daily_budget": budget}
                daily_counts[(name, budget)] = {r[0]: r[1] for r in db.execute("SELECT substr(f.event_time,1,10),COALESCE(SUM(CASE WHEN r.transaction_id IS NOT NULL THEN l.is_fraud ELSE 0 END),0) FROM facts f JOIN outcomes.labels l USING(transaction_id) LEFT JOIN reviews r ON f.transaction_id=r.transaction_id AND r.model=? AND r.budget=? WHERE l.split='test' GROUP BY substr(f.event_time,1,10)", (name, budget))}
            strategies[name] = {"average_precision": ap, "independent_ap_verified": True, "roc_auc": float(roc_auc_score(y, scores)) if len(set(y)) == 2 else None, "daily_review": per_budget}
        comparison = {}
        budget = contract["primary_budget"]
        for name in strategies:
            comparison[name] = {}
            for baseline in contract["comparison_baselines"]:
                days = sorted(daily_counts[(name, budget)])
                difference = np.array([daily_counts[(name, budget)][d] - daily_counts[(baseline, budget)][d] for d in days])
                generator = np.random.default_rng(contract["bootstrap"]["seed"])
                samples = generator.choice(difference, size=(contract["bootstrap"]["repetitions"], len(days)), replace=True).sum(axis=1)
                comparison[name][baseline] = {"paired_net_tp": int(difference.sum()), "day_bootstrap_interval_95": np.quantile(samples, [.025, .975]).tolist(), "uncertainty_scope": "Day resampling sensitivity only; longer dependence remains."}
    return {"status": "final_time_holdout_evaluated", "data_kind": "IBM_TabFormer_synthetic", "period": "2019", "rows": rows, "positives": int(positives),
            "primary_model_frozen_before_test": contract["primary_model"], "primary_budget": contract["primary_budget"], "strategies": strategies,
            "paired_comparison": comparison, "contract_sha256": sha256_file(directory / "contract.json"), "predictions_manifest_sha256": sha256_file(directory / "predictions.json"),
            "outcomes_opened_sha256": sha256_file(directory / "outcomes-opened.json"), "selection_after_final_test": False, "deployed": False, "limits": contract["limits"]}
