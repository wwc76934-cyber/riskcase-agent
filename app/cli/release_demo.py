"""Prepare label-blind neural alerts, grounded investigation and a reviewable local UI package."""

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import heapq
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

import numpy as np
import pandas as pd

from app.risk.case_snapshot import export_case
from app.risk.detector_explanation import FrozenNeuralEnsemble, explain_case, load_feature_input, models_from_neural_run, render_explanation
from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import select_users, write_json
from app.service.runtime import ResearchService, Settings, Worker, fingerprint
from app.service.store import digest
from scripts.verify_mixed import verify


TIE_SEED = "riskcase-review-ties-v1"
USER_SEED = "riskcase-release-users-v1"
QUESTION = "全面检查这五条线索"


def read_json(path): return json.loads(Path(path).read_text(encoding="utf-8"))


def checked_file(directory, name, expected):
    path = (Path(directory) / name).resolve()
    if not path.is_relative_to(Path(directory).resolve()) or sha256_file(path) != expected: raise ValueError("frozen_artifact_changed:" + name)
    return path


def load_label_free_development(neural_run, features_manifest, cohort_report):
    """Match training's ORDER BY transaction_id, with no label join or label connection."""
    neural_run, features_manifest, cohort_report = map(Path, (neural_run, features_manifest, cohort_report))
    report, data = read_json(neural_run / "report.json"), read_json(neural_run / "data.json")
    if report.get("status") != "neural_v2_development_complete" or report.get("final_test_read") is not False:
        raise ValueError("frozen_2018_development_model_required")
    if report["selected"]["seeds"] != [42,43,44] or [r["seed"] for r in report["seeds"]] != [42,43,44]: raise ValueError("three_seed_confirmation_required")
    required = {"data.json", "ensemble-development-scores.npy"} | {
        f"confirmation-seed-{seed}/{name}" for seed in (42,43,44)
        for name in ("development-scores.npy", "model/model.pt", "model/preprocessor.joblib")}
    if not required <= set(report["artifact_sha256"]): raise ValueError("required_frozen_scores_or_models_unhashed")
    for name, expected in report["artifact_sha256"].items(): checked_file(neural_run, name, expected)
    features, cohort = read_json(features_manifest), read_json(cohort_report)
    feature_db, facts_db = features_manifest.parent / "features.sqlite", cohort_report.parent / "dataset/facts.sqlite"
    if features.get("status") != "features_built" or features.get("labels_read") is not False: raise ValueError("label_free_features_required")
    if (sha256_file(features_manifest) != data["manifest_sha256"] or sha256_file(feature_db) != data["features_sha256"]
            or features["features_sha256"] != data["features_sha256"]): raise ValueError("model_feature_snapshot_differs")
    if sha256_file(cohort_report) != features["cohort_report_sha256"] or sha256_file(facts_db) != features["facts_sha256"] or features["facts_sha256"] != cohort["artifact_sha256"]["dataset/facts.sqlite"]:
        raise ValueError("source_snapshot_differs")
    if cohort.get("source_csv_fully_scanned") is not True: raise ValueError("complete_source_scan_required")
    columns = ",".join("f." + c for c in MODEL_COLUMNS)
    with closing(sqlite3.connect(feature_db.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("ATTACH DATABASE ? AS source", (facts_db.resolve().as_uri() + "?mode=ro",))
        # No evaluation/label database is opened. All SQL record predicates explicitly select 2018.
        frame = pd.read_sql_query(f"""SELECT f.transaction_id,f.user_id,f.event_time,f.split,f.history_30d_complete,{columns}
            FROM features f JOIN source.transactions t ON f.transaction_id=t.transaction_id
                AND f.user_id=t.user_id AND f.event_time=t.event_time
            WHERE f.split='validation' AND t.split='validation'
              AND f.event_time>='2018-01-01' AND f.event_time<'2019-01-01'
            ORDER BY f.transaction_id""", db)
    if len(frame) != report["development_rows"] or frame.transaction_id.duplicated().any() or not len(frame): raise ValueError("development_row_alignment_differs")
    if not frame.history_30d_complete.eq(1).all() or not frame.event_time.between("2018-01-01", "2019-01-01", inclusive="left").all(): raise ValueError("development_coverage_or_period_differs")
    scores = []
    for seed in (42,43,44):
        values = np.load(neural_run / f"confirmation-seed-{seed}/development-scores.npy", allow_pickle=False)
        if values.shape != (len(frame),) or not np.isfinite(values).all() or (values < 0).any() or (values > 1).any(): raise ValueError("invalid_seed_scores")
        scores.append(values)
    ensemble = np.load(neural_run / "ensemble-development-scores.npy", allow_pickle=False)
    if ensemble.shape != (len(frame),) or not np.array_equal(ensemble, np.mean(scores, axis=0)): raise ValueError("three_seed_mean_differs")
    return frame[list(MODEL_COLUMNS)], frame[["transaction_id", "user_id", "event_time", "split"]], scores, ensemble, {
        "neural_report_sha256": sha256_file(neural_run / "report.json"), "features_manifest_sha256": sha256_file(features_manifest),
        "features_sha256": features["features_sha256"], "cohort_report_sha256": sha256_file(cohort_report), "source_facts_sha256": features["facts_sha256"],
        "validation_rows": len(frame), "row_order": "transaction_id ascending, same as load_split", "labels_read": False,
        "final_2019_records_selected": False,
    }


def verify_score_alignment(scorer, frame, saved_seed_scores):
    """Recompute all model outputs, not merely trust a positional array's length."""
    if len(scorer.predictors) != 3: raise ValueError("three_model_predictors_required")
    differences = []
    for model, expected in zip(scorer.predictors, saved_seed_scores):
        actual = np.asarray(model.predict(frame))
        if actual.shape != expected.shape or not np.isfinite(actual).all(): raise ValueError("rescored_shape_or_values_differ")
        difference = float(np.max(np.abs(actual - expected)))
        if difference > 1e-6: raise ValueError("score_order_or_model_prediction_differs")
        differences.append(difference)
    return {"status": "all_2018_rows_rescored", "rows_per_model": len(frame), "models": 3,
            "max_absolute_differences": differences, "tolerance": 1e-6, "labels_read": False}


def rank_alerts(metadata, scores, *, daily_budget=10):
    if daily_budget != 10 or len(metadata) != len(scores) or not np.isfinite(scores).all(): raise ValueError("frozen_top10_ranking_required")
    heaps = defaultdict(list)
    for position, (identity, user, stamp, split) in enumerate(metadata.itertuples(index=False, name=None)):
        if split != "validation" or not "2018-01-01" <= stamp < "2019-01-01": raise ValueError("only_2018_queue_allowed")
        tie = hashlib.sha256(f"{TIE_SEED}\0{identity}".encode()).hexdigest()
        item = (float(scores[position]), tie, identity, user, stamp)
        heap = heaps[stamp[:10]]
        if len(heap) < daily_budget: heapq.heappush(heap, item)
        elif item[:3] > heap[0][:3]: heapq.heapreplace(heap, item)
    result = []
    for day in sorted(heaps):
        for rank, (score, tie, identity, user, stamp) in enumerate(sorted(heaps[day], key=lambda r:r[:3], reverse=True), 1):
            result.append({"transaction_id": identity, "user_id": user, "event_time": stamp, "score": score,
                           "day_rank": rank, "tie_hash": tie})
    return result


def prepare(root, output, *, neural_run=None, features_manifest=None, cohort_report=None, users=4, scorer_factory=FrozenNeuralEnsemble):
    root, output = Path(root).resolve(), Path(output).resolve()
    if type(users) is not int or not 1 <= users <= 8: raise ValueError("one_to_eight_representative_users_required")
    if output.exists(): raise FileExistsError("new_release_directory_required")
    neural_run = Path(neural_run or root / "runs/neural-v2-development-01").resolve()
    features_manifest = Path(features_manifest or root / "data/features/features-b9692149f1c1/manifest.json").resolve()
    cohort_report = Path(cohort_report or root / "data/cohorts/cohort-3bfdbaab486f/build-report.json").resolve()
    X, metadata, seeds, ensemble, provenance = load_label_free_development(neural_run, features_manifest, cohort_report)
    models = models_from_neural_run(neural_run)
    scorer = scorer_factory(models, trusted_local=True)
    alignment = verify_score_alignment(scorer, X, seeds)
    queue = rank_alerts(metadata, ensemble)
    selected = select_users(queue, count=users, seed=USER_SEED, excluded=())
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "started.json", {"status": "preparing", "labels_read": False, "real_LLM_requests": 0,
               "provenance": provenance, "users": users, "user_selection_seed": USER_SEED})
    write_json(output / "alignment.json", alignment)
    write_json(output / "daily-review.json", queue)
    write_json(output / "selected-alerts.json", selected)
    cohort = read_json(cohort_report)
    catalog, entries = {}, []
    source = cohort_report.parent / "dataset/facts.sqlite"
    for index, alert in enumerate(selected):
        identity = f"neural-2018-user-{index:02d}"
        directory = output / "cases" / identity
        case = export_case(directory, source_database=source, expected_source_sha256=provenance["source_facts_sha256"],
                           transaction_id=alert["transaction_id"],
                           alert={"transaction_id": alert["transaction_id"], "score": alert["score"], "day_rank": alert["day_rank"],
                                  "detector": "neural_v2_ensemble", "score_kind": "uncalibrated_ranking_sigmoid_not_probability",
                                  "model_artifacts_sha256": digest(models), "model_report_sha256": provenance["neural_report_sha256"],
                                  "selection": "Label-blind 2018 end-of-day top10, earliest queued alert per user, fixed seeded user selection; not a realtime blocking rule."},
                           coverage_start=cohort["coverage"]["warmup_start"], coverage_end=cohort["coverage"]["end"],
                           full_source_scanned=True, source_report_sha256=provenance["cohort_report_sha256"])
        bound, feature_proof = load_feature_input(features_manifest, case)
        sensitivity = explain_case(case, bound, scorer, model_metadata=scorer.metadata, input_provenance=feature_proof)
        difference = abs(sensitivity["original"]["score"] - alert["score"])
        if difference > 1e-6: raise ValueError("selected_alert_and_recomputed_model_score_differ")
        write_json(directory / "bound-input.json", bound)
        write_json(directory / "sensitivity.json", sensitivity)
        (directory / "sensitivity.md").write_text(render_explanation(sensitivity), encoding="utf-8")
        catalog[identity] = str(case)
        entries.append({"case_id": identity, "manifest": str(case.relative_to(output)).replace("\\", "/"),
                        "user_id": alert["user_id"], "transaction_id": alert["transaction_id"], "score": alert["score"],
                        "day_rank": alert["day_rank"], "sensitivity_evidence_id": sensitivity["evidence_id"],
                        "matching_model_artifacts_checked_in_release_layer": True, "recomputed_score_absolute_difference": difference})
    write_json(output / "catalog.json", catalog)
    service_dir = output / "service"; service_dir.mkdir()
    token = secrets.token_urlsafe(32)
    (service_dir / "auth.token").write_text(token, encoding="ascii")
    service = ResearchService(Settings(root, service_dir, {k:Path(v) for k,v in catalog.items()}, token, allow_live=False))
    code_fingerprint = fingerprint(root)
    with SourceAudit(cohort_report) as audit:
        for entry in entries:
            identity, created = service.submit("release-" + entry["case_id"], case_id=entry["case_id"], task_type="natural_v2", mode="offline", question={"text": QUESTION})
            if not created: raise ValueError("unexpected_existing_release_job")
            row = Worker(service).step()
            if row["id"] != identity or row["state"] != "awaiting_plan_confirmation": raise ValueError("offline_scope_proposal_failed")
            service.confirm_plan(identity, expected_version=row["version"], result_hash=row["result_hash"],
                                 plan_hash=row["result"]["plan_hash"], decision="confirm_plan", request_key="simulated-confirm-" + entry["case_id"])
            row = Worker(service).step()
            if row["id"] != identity or row["state"] != "awaiting_review": raise ValueError("confirmed_deterministic_investigation_failed")
            proof = verify(service.artifact(identity, "summary.json"), audit)
            if not proof["task_passed"] or proof["extra_distinct_queries"] or proof["missing_required_queries"]: raise ValueError("source_sql_investigation_failed")
            summary = read_json(service.artifact(identity, "summary.json"))
            if summary["real_LLM_requests"] != 0: raise ValueError("offline_release_must_not_call_llm")
            entry.update(job_id=identity, state=row["state"], question=QUESTION,
                         report=str(service.artifact(identity, "report.md").relative_to(output)).replace("\\", "/"),
                         summary=str(service.artifact(identity, "summary.json").relative_to(output)).replace("\\", "/"),
                         sql_verification=proof, scope_confirmation="SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW",
                         client_mode="KEYWORD_SCRIPT_NOT_REAL_LLM", policy_approved=False, deployed=False)
    if fingerprint(root) != code_fingerprint: raise ValueError("service_runtime_changed_during_preparation")
    write_json(output / "cases.json", entries)
    overview = ["# 深度预警到调查：本地研究演示包", "", "真实IBM合成源的冻结2018开发记录；不是手工fixture、独立泛化测试或生产风控。",
                "", f"3seed冻结神经分数逐行重算通过，共{len(metadata):,}行；每日最多10笔，固定seed选{users}个不同用户代表。",
                "未读取标签或2019记录。队列是收盘后的批量审核，不能解释为交易发生时已经知道全天排名。",
                "", "计划使用离线关键词脚本，不是本次真实LLM能力成绩。范围确认由脚本模拟，报告仍待审阅，真实金融候选库未改动。", "",
                "| 案例 | 分数（非概率） | 当日排名 | 调查报告 | 神经输入敏感性 |", "|---|---:|---:|---|---|"]
    for entry in entries:
        overview.append(f"| {entry['case_id']} | {entry['score']:.8f} | {entry['day_rank']} | [报告]({entry['report']}) | [单输入敏感性](cases/{entry['case_id']}/sensitivity.md) |")
    overview += ["", "## 打开同一批报告的审阅界面", "", "在仓库根目录运行：", "", "```powershell",
                 f'.\\scripts\\run_project.ps1 -m app.cli.workbench --directory "{service_dir}" --catalog "{output / "catalog.json"}"', "```", "",
                 "启动后到127.0.0.1:8765，使用service/auth.token中的本地访问令牌；不要把它当作模型API密钥或公开提交。默认不开启真实模型。",
                 "进入已有任务即可查看待审阅报告；接受报告不批准金融规则。也可在注册case上提交新问题。",
                 "", "manifest.json记录准备完成时的不可变材料SHA及单独的可变服务库快照SHA。界面后续审阅会正常改变jobs.sqlite，不能把准备快照当成持续不变的数据库签名。",
                 "敏感性不是因果或真实反事实；case原生模块保守显示模型来源未独立核实，本包在装配层额外核对同一冻结模型文件及分数，见cases.json配对差异。"]
    (output / "README.md").write_text("\n".join(overview)+"\n", encoding="utf-8")
    mutable = {"service/jobs.sqlite", "service/auth.token"}
    artifacts = {str(p.relative_to(output)).replace("\\", "/"): sha256_file(p) for p in output.rglob("*") if p.is_file()
                 and str(p.relative_to(output)).replace("\\", "/") not in mutable}
    manifest = {"status": "neural_investigation_release_prepared", "data_kind": "IBM_TabFormer_synthetic", "development_period": "2018",
                "validation_rows": len(metadata), "queue_rows": len(queue), "representative_users": users, "daily_budget": 10,
                "source_provenance": provenance, "models": scorer.metadata, "alignment": alignment, "service_runtime_fingerprint": code_fingerprint,
                "user_selection_seed": USER_SEED, "tie_seed": TIE_SEED, "cases": entries,
                "scope_confirmation": "SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW", "client_mode": "KEYWORD_SCRIPT_NOT_REAL_LLM",
                "artifact_sha256": artifacts, "mutable_runtime_snapshot_sha256": {name:sha256_file(output/name) for name in mutable},
                "code_file_sha256": {"app/cli/release_demo.py": sha256_file(root / "app/cli/release_demo.py")},
                "labels_read": False, "final_2019_records_selected": False, "real_LLM_requests": 0, "policy_approved": False, "deployed": False,
                "limitations": ["Development showcase selection, not independent user/effectiveness evaluation.",
                               "Model ranking sigmoid is uncalibrated; neither score nor feature perturbation establishes fraud.",
                               "End-of-day top10 membership uses the completed day's scores; not an online blocking policy.",
                               "Source-local time/currency/ingestion availability remain unknown.",
                               "Scripted plan confirmation is not human review; reports stay awaiting_review."]}
    write_json(output / "manifest.json", manifest)
    verify_package(output, include_runtime_snapshot=True)
    return manifest


def verify_package(output, *, include_runtime_snapshot=False):
    output = Path(output).resolve(); manifest = read_json(output / "manifest.json")
    for name, expected in manifest["artifact_sha256"].items(): checked_file(output, name, expected)
    if include_runtime_snapshot:
        for name, expected in manifest["mutable_runtime_snapshot_sha256"].items(): checked_file(output, name, expected)
    return {"status": "prepared_material_hashes_verified", "artifacts": len(manifest["artifact_sha256"]),
            "runtime_snapshot_checked": include_runtime_snapshot, "labels_read": False}


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--neural-run", type=Path)
    parser.add_argument("--features-manifest", type=Path)
    parser.add_argument("--cohort-report", type=Path)
    parser.add_argument("--users", type=int, default=4)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--include-runtime-snapshot", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_package(args.verify, include_runtime_snapshot=args.include_runtime_snapshot), ensure_ascii=False, indent=2)); return
    import torch
    torch.set_num_threads(4)
    directory = args.output or root / "runs" / ("release-demo-" + uuid4().hex[:12])
    result = prepare(root, directory, neural_run=args.neural_run, features_manifest=args.features_manifest, cohort_report=args.cohort_report, users=args.users)
    print(json.dumps({"output_directory": str(directory), "status": result["status"], "validation_rows": result["validation_rows"],
                      "queue_rows": result["queue_rows"], "users": result["representative_users"],
                      "case_ids": [c["case_id"] for c in result["cases"]], "real_LLM_requests": 0,
                      "reports_awaiting_review": len(result["cases"]), "policy_approved": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
