"""Frozen 2018 rule research around neural ensemble ranking; never approves a rule."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess

import numpy as np

from app.risk.candidate_rules import FIELDS, compile_candidate
from app.risk.capacity_policy import TIE_SEED, allocate_day, outcome_metrics, tie_key
from app.risk.history import sha256_file
from app.risk.review_store import digest
from app.risk.source_merchant_pack import write_json


POLICIES = ("neural", "rule_only", "reserve_20pct", "rule_first")
CODE = ("app/risk/capacity_v2.py", "app/cli/capacity_v2.py", "scripts/verify_capacity_v2.py",
        "app/risk/capacity_policy.py", "app/risk/candidate_rules.py", "app/risk/review_store.py",
        "app/risk/history.py", "app/risk/source_merchant_pack.py")


def candidate_state(path, identity):
    """The actual review database is always opened read-only, without a store constructor."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT state_json FROM candidates WHERE id=?", (identity,)).fetchone()
    if row is None: raise ValueError("candidate_not_found")
    state = json.loads(row[0])
    if state["candidate_id"] != identity or state["version"] != 2 or state["status"] != "pending_review" or state["deployed"]:
        raise ValueError("unchanged_pending_candidate_v2_required")
    if state["package_hash"] != digest(state["package"]): raise ValueError("candidate_package_hash_differs")
    compile_candidate(state["package"]["rule"])
    return state


def freeze(root, directory, *, neural_run=None, reviews_database=None, candidate_id="case-467716-rule"):
    root, directory = Path(root), Path(directory)
    neural_run = Path(neural_run or root / "runs/neural-v2-development-01")
    reviews_database = Path(reviews_database or root / "runs/reviews.sqlite")
    report_path, data_path = neural_run / "report.json", neural_run / "data.json"
    report, data = (json.loads(p.read_text(encoding="utf-8")) for p in (report_path, data_path))
    if report["status"] != "neural_v2_development_complete" or report["final_test_read"] or report["selected"]["seeds"] != [42, 43, 44]:
        raise ValueError("three_seed_development_ensemble_required")
    scores = neural_run / "ensemble-development-scores.npy"
    seeds = [neural_run / f"confirmation-seed-{seed}/development-scores.npy" for seed in (42, 43, 44)]
    for path in (data_path, scores, *seeds, neural_run / "selected-recipe.json", neural_run / "protocol.json"):
        if sha256_file(path) != report["artifact_sha256"].get(str(path.relative_to(neural_run)).replace("\\", "/")):
            raise ValueError("neural_run_artifact_changed")
    features_manifest = Path(data["manifest"])
    feature_data = json.loads(features_manifest.read_text(encoding="utf-8"))
    features = features_manifest.parent / "features.sqlite"
    cohort = Path(feature_data["cohort_report"])
    cohort_data = json.loads(cohort.read_text(encoding="utf-8"))
    if (sha256_file(features_manifest) != data["manifest_sha256"] or sha256_file(features) != data["features_sha256"]
            or sha256_file(features) != feature_data["features_sha256"] or sha256_file(cohort) != feature_data["cohort_report_sha256"]
            or feature_data["labels_read"] is not False): raise ValueError("neural_feature_binding_changed")
    labels = cohort.parent / "dataset/evaluation-labels.sqlite"
    if data["labels_sha256"] != cohort_data["artifact_sha256"]["dataset/evaluation-labels.sqlite"]:
        raise ValueError("label_version_binding_changed")
    state = candidate_state(reviews_database, candidate_id)
    if report["development_rows"] != cohort_data["partitions"]["validation"]["rows"] or report["development_positives"] != cohort_data["partitions"]["validation"]["fraud_rows"]:
        raise ValueError("development_population_differs")
    directory.mkdir(parents=True, exist_ok=False)
    inputs = {name: {"path": str(path.resolve()), "sha256": sha256_file(path)} for name, path in (
        ("neural_report", report_path), ("neural_data", data_path), ("neural_protocol", neural_run / "protocol.json"),
        ("selected_recipe", neural_run / "selected-recipe.json"), ("scores", scores),
        ("seed_42", seeds[0]), ("seed_43", seeds[1]), ("seed_44", seeds[2]),
        ("features_manifest", features_manifest), ("features", features), ("cohort", cohort))}
    protocol = {"version": 2, "status": "protocol_frozen", "frozen_at": datetime.now(timezone.utc).isoformat(),
                "data_kind": "IBM_TabFormer_synthetic", "baseline_kind": "neural_v2_ensemble", "split": "validation",
                "period": ["2018-01-01", "2019-01-01"], "budgets": [5, 10, 20], "primary_budget": 10,
                "policies": list(POLICIES), "tie_seed": TIE_SEED, "rule": state["package"]["rule"],
                "candidate": {"id": candidate_id, "version": 2, "status": "pending_review", "package_hash": state["package_hash"],
                              "state_hash": digest(state), "database": str(reviews_database.resolve()), "database_sha256": sha256_file(reviews_database)},
                "inputs": inputs, "labels_path": str(labels.resolve()), "labels_sha256": data["labels_sha256"],
                "expected_rows": report["development_rows"], "expected_positives": report["development_positives"],
                "expected_neural_top10_tp": report["ensemble"]["daily_review"]["true_positives"],
                "selected_recipe": report["selected"],
                "score_alignment": "Numpy vector indexed by 2018 feature rows ORDER BY transaction_id, identical to neural training load_split ordering.",
                "ranking": {"neural": "Neural ensemble score descending.", "rule_only": "Rule matches only, positive amount descending; unused seats remain empty.",
                            "reserve_20pct": "Reserve floor(K/5) highest-neural-score rule matches, then fill by neural score without duplicates.",
                            "rule_first": "Rule matches first, neural score descending within both groups."},
                "legacy_adapter": "allocate_day argument hgb_score is an internal compatibility field carrying neural ensemble score; it is not an HGB prediction.",
                "code_file_sha256": {name: sha256_file(root / name) for name in CODE},
                "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                "limits": ["2018 was repeatedly used for development and neural model selection; no untouched holdout claim.",
                           "The existing candidate version 2 is reused unchanged, with no threshold search or automatic adoption.",
                           "2019 records/outcomes are not queried by these stages; only 2018 validation outcomes are used.",
                           "Same end-of-day capacity upper bound; rule-only can leave seats unused.",
                           "Synthetic outcomes and unknown label-availability/currency; no operational loss, profit, or real-time effectiveness claim.",
                           "This creates an unregistered research package pending review, not a new actual candidate approval/version."]}
    write_json(directory / "protocol.json", protocol)
    return protocol


def load_protocol(root, directory):
    root, directory = Path(root), Path(directory)
    protocol = json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    if protocol["baseline_kind"] != "neural_v2_ensemble" or protocol["split"] != "validation" or protocol["period"] != ["2018-01-01", "2019-01-01"]:
        raise ValueError("development_neural_protocol_required")
    if protocol["budgets"] != [5, 10, 20] or protocol["primary_budget"] != 10 or protocol["policies"] != list(POLICIES) or protocol["tie_seed"] != TIE_SEED:
        raise ValueError("frozen_capacity_policy_changed")
    for name, expected in protocol["code_file_sha256"].items():
        if sha256_file(root / name) != expected: raise ValueError("capacity_code_changed")
    for item in protocol["inputs"].values():
        if sha256_file(Path(item["path"])) != item["sha256"]: raise ValueError("capacity_input_changed")
    binding = protocol["candidate"]
    if sha256_file(Path(binding["database"])) != binding["database_sha256"]: raise ValueError("actual_review_database_changed")
    state = candidate_state(binding["database"], binding["id"])
    if digest(state) != binding["state_hash"] or state["package_hash"] != binding["package_hash"] or state["package"]["rule"] != protocol["rule"]:
        raise ValueError("candidate_version_or_rule_changed")
    return protocol


def aligned_scores(protocol):
    scores = np.load(protocol["inputs"]["scores"]["path"], allow_pickle=False)
    seeds = [np.load(protocol["inputs"][f"seed_{seed}"]["path"], allow_pickle=False) for seed in (42, 43, 44)]
    if scores.shape != (protocol["expected_rows"],) or any(s.shape != scores.shape for s in seeds): raise ValueError("neural_score_shape_differs")
    if not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any() or any(not np.isfinite(s).all() or (s < 0).any() or (s > 1).any() for s in seeds):
        raise ValueError("bounded_finite_neural_scores_required")
    if not np.array_equal(scores, np.mean(seeds, axis=0)): raise ValueError("declared_three_seed_mean_differs")
    return scores


def allocate_neural_day(rows, budget, policy):
    if policy not in POLICIES: raise ValueError("unsupported_neural_capacity_policy")
    # Only this local adapter uses the historic parameter name; persisted schema never does.
    adapted = []
    for row in rows:
        if "label" in row or "is_fraud" in row: raise ValueError("outcomes_forbidden_during_allocation")
        adapted.append({"transaction_id": row["transaction_id"], "day": row["day"], "amount_cents": row["amount_cents"],
                        "rule_match": row["rule_match"], "tie": row["tie"], "hgb_score": row["neural_score"]})
    return allocate_day(adapted, budget, "hgb" if policy == "neural" else policy)


def materialize(root, directory):
    directory = Path(directory)
    protocol = load_protocol(root, directory)
    population, plans = directory / "ranking-inputs.sqlite", directory / "planned-reviews.sqlite"
    if population.exists() or plans.exists() or (directory / "labels-opened.json").exists(): raise ValueError("capacity_plans_already_exist")
    scores, rule = aligned_scores(protocol), compile_candidate(protocol["rule"])
    fields = sorted(FIELDS)
    with closing(sqlite3.connect(Path(protocol["inputs"]["features"]["path"]).resolve().as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(population)) as output, output:
        source.row_factory = sqlite3.Row
        output.execute("CREATE TABLE population(transaction_id TEXT PRIMARY KEY,event_time TEXT,day TEXT,channel TEXT,amount_cents INTEGER,neural_score REAL,rule_match INTEGER,tie TEXT)")
        cursor = source.execute("SELECT transaction_id,event_time,channel," + ",".join(fields) + " FROM features WHERE split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01' ORDER BY transaction_id")
        count = 0
        for count, record in enumerate(cursor, 1):
            if count > len(scores): raise ValueError("features_exceed_frozen_neural_population")
            record = dict(record); identity, stamp = record["transaction_id"], record["event_time"]
            output.execute("INSERT INTO population VALUES (?,?,?,?,?,?,?,?)", (identity, stamp, stamp[:10], record["channel"], record["amount_cents"], float(scores[count-1]), int(rule(record)), tie_key(identity)))
        if count != len(scores): raise ValueError("feature_score_population_differs")
        output.execute("CREATE INDEX population_day ON population(day,transaction_id)")
    with closing(sqlite3.connect(population.resolve().as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(plans)) as output, output:
        source.row_factory = sqlite3.Row
        output.execute("CREATE TABLE reviews(budget INTEGER,policy TEXT,transaction_id TEXT,day TEXT,rank INTEGER,PRIMARY KEY(budget,policy,transaction_id),UNIQUE(budget,policy,day,rank))")
        days = [r[0] for r in source.execute("SELECT DISTINCT day FROM population ORDER BY day")]
        for day in days:
            rows = [dict(r) for r in source.execute("SELECT * FROM population WHERE day=? ORDER BY transaction_id", (day,))]
            for row in rows: row["rule_match"] = bool(row["rule_match"])
            for budget in protocol["budgets"]:
                for policy in POLICIES:
                    selected = allocate_neural_day(rows, budget, policy)
                    output.executemany("INSERT INTO reviews VALUES (?,?,?,?,?)", [(budget, policy, r["transaction_id"], day, rank) for rank, r in enumerate(selected, 1)])
    manifest = {"status": "label_free_neural_capacity_lists_frozen", "baseline_kind": "neural_v2_ensemble", "labels_read": False,
                "protocol_sha256": sha256_file(directory / "protocol.json"), "population_sha256": sha256_file(population),
                "plans_sha256": sha256_file(plans), "rows": count, "days": len(days), "frozen_at": datetime.now(timezone.utc).isoformat(),
                "actual_candidate_version_unchanged": 2}
    write_json(directory / "plan-manifest.json", manifest)
    return manifest


def load_plans(root, directory):
    directory = Path(directory)
    protocol = load_protocol(root, directory)
    manifest = json.loads((directory / "plan-manifest.json").read_text(encoding="utf-8"))
    if manifest["labels_read"] is not False or manifest["protocol_sha256"] != sha256_file(directory / "protocol.json"):
        raise ValueError("pre_outcome_plan_binding_differs")
    if sha256_file(directory / "ranking-inputs.sqlite") != manifest["population_sha256"] or sha256_file(directory / "planned-reviews.sqlite") != manifest["plans_sha256"]:
        raise ValueError("frozen_neural_capacity_lists_changed")
    return protocol, manifest


def evaluate(root, directory):
    directory = Path(directory)
    protocol, _ = load_plans(root, directory)
    if (directory / "report.json").exists() or (directory / "labels-opened.json").exists(): raise ValueError("capacity_outcomes_already_opened")
    labels_path = Path(protocol["labels_path"])
    if sha256_file(labels_path) != protocol["labels_sha256"]: raise ValueError("development_outcomes_changed")
    with (directory / "labels-opened.json").open("x", encoding="utf-8") as marker:
        json.dump({"period": "2018", "plan_manifest_sha256": sha256_file(directory / "plan-manifest.json"),
                   "opened_at": datetime.now(timezone.utc).isoformat(), "no_rule_or_allocation_tuning": True}, marker)
    with closing(sqlite3.connect((directory / "ranking-inputs.sqlite").resolve().as_uri()+"?mode=ro", uri=True)) as db:
        population = dict(db.execute("SELECT transaction_id,day FROM population"))
    with closing(sqlite3.connect(labels_path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        raw = dict(db.execute("SELECT transaction_id,is_fraud FROM labels WHERE split='validation'"))
    if set(raw) != set(population) or any(type(y) is not int or y not in (0, 1) for y in raw.values()): raise ValueError("complete_development_outcomes_required")
    labels = {identity: bool(y) for identity, y in raw.items()}
    if sum(labels.values()) != protocol["expected_positives"]: raise ValueError("frozen_development_positive_count_differs")
    metrics, monthly = {}, {}
    with closing(sqlite3.connect((directory / "planned-reviews.sqlite").resolve().as_uri()+"?mode=ro", uri=True)) as db:
        for budget in protocol["budgets"]:
            baseline = {r[0] for r in db.execute("SELECT transaction_id FROM reviews WHERE budget=? AND policy='neural'", (budget,))}
            for policy in POLICIES:
                selected = {r[0] for r in db.execute("SELECT transaction_id FROM reviews WHERE budget=? AND policy=?", (budget, policy))}
                metrics[f"{budget}/{policy}"] = outcome_metrics(population, selected, baseline, labels, budget=budget)
                if budget == protocol["primary_budget"]:
                    monthly[policy] = {}
                    for month in sorted({day[:7] for day in population.values()}):
                        subset = {i: d for i, d in population.items() if d.startswith(month)}
                        monthly[policy][month] = outcome_metrics(subset, selected & subset.keys(), baseline & subset.keys(), {i: labels[i] for i in subset}, budget=budget)
    if metrics["10/neural"]["true_positives"] != protocol["expected_neural_top10_tp"]: raise ValueError("source_neural_ensemble_top10_differs")
    result = {"status": "neural_capacity_development_evaluated", "baseline_kind": "neural_v2_ensemble", "data_kind": protocol["data_kind"],
              "protocol_sha256": sha256_file(directory / "protocol.json"), "plan_manifest_sha256": sha256_file(directory / "plan-manifest.json"),
              "labels_opened_sha256": sha256_file(directory / "labels-opened.json"), "metrics": metrics, "primary_monthly": monthly,
              "real_LLM_requests": 0, "final_test_evaluated": False, "human_approved": False, "deployed": False,
              "actual_candidate_version": 2, "research_review_status": "pending_review_unregistered_material", "limits": protocol["limits"]}
    write_json(directory / "report.json", result)
    return result
