"""Bound-case single-input score sensitivity; never causal or a fraud verdict."""

from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import math
import json
from numbers import Real
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import COLUMNS, local_time
from app.risk.history import sha256_file
from app.risk.ml_models import MODEL_COLUMNS
from app.service.store import digest
from app.tools.case_tools import CaseTools


VERSION = "detector-single-input-sensitivity-v1"
WINDOWS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}
CAUTIONS = [
    "Single raw-input perturbations, not causal effects or verified real-world counterfactual transactions.",
    "Changing one aggregate without rebuilding its history can create a logically inconsistent feature vector.",
    "The model recomputes its own engineered transforms; a single raw column may affect several transformed inputs.",
    "Score differences are not additive feature contributions, calibrated fraud probabilities or evidence of fraud.",
    "This recomputes the supplied frozen neural model; the model that originally raised the case alert may differ.",
    "No claim that modifying a person's behavior will produce these scores or reduce real-world fraud.",
    "Case completeness is synthetic-source completeness; ingestion availability, currency and timezone remain unknown.",
]


def _json_file(path, maximum=200000):
    path = Path(path)
    if path.stat().st_size > maximum: raise ValueError("bounded_json_artifact_required")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    def invalid(_): raise ValueError("nonfinite_json_number")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def _case(manifest_path):
    path = Path(manifest_path)
    tools = CaseTools(path)
    with closing(sqlite3.connect((path.parent / "facts.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        if {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {"transactions"}:
            raise ValueError("bound_case_tables_required")
        if tuple(r[1] for r in db.execute("PRAGMA table_info(transactions)")) != COLUMNS:
            raise ValueError("bound_case_schema_required")
    return tools


def _features(bound_input, tools):
    if not isinstance(bound_input, dict) or set(bound_input) != {"transaction_id", "user_id", "event_time", "features"}:
        raise ValueError("explicit_bound_model_input_required")
    if (bound_input["transaction_id"] != tools.target["transaction_id"] or bound_input["user_id"] != tools.manifest["user_id"]
            or local_time(bound_input["event_time"]) != tools.cutoff):
        raise ValueError("model_input_case_binding_differs")
    features = bound_input["features"]
    if not isinstance(features, dict) or set(features) != set(MODEL_COLUMNS): raise ValueError("exact_model_columns_required")
    for name, value in features.items():
        if name in ("channel", "mcc"):
            if value is not None and (type(value) is not str or len(value) > 100): raise ValueError("invalid_category")
        elif name == "seconds_since_previous_in_snapshot" and value is None:
            continue
        elif type(value) is not int or abs(value) > 10**16:
            raise ValueError("finite_integer_raw_feature_required")
    for name in ("merchant_seen_30d", "has_history_30d"):
        if features[name] not in (0, 1): raise ValueError("binary_history_feature_required")
    if not 0 <= features["hour"] <= 23 or not 0 <= features["weekday"] <= 6: raise ValueError("invalid_calendar_feature")
    counts = [features["count_" + window] for window in WINDOWS]
    if any(c < 0 for c in counts) or counts != sorted(counts): raise ValueError("inconsistent_window_counts")
    if bool(features["count_30d"]) != bool(features["has_history_30d"]): raise ValueError("inconsistent_history_flag")
    if features["merchant_seen_30d"] and not features["has_history_30d"]: raise ValueError("inconsistent_merchant_history")
    previous = features["seconds_since_previous_in_snapshot"]
    if previous is not None and previous <= 0: raise ValueError("strictly_prior_previous_event_required")
    return deepcopy(features)


def validate_input(manifest_path, bound_input):
    """Verify every reconstructable feature; explicitly identify unreconstructable ones."""
    tools = _case(manifest_path)
    features = _features(bound_input, tools)
    target, observed, complete = tools.target, {}, {}
    observed.update(amount_cents=target["amount_cents"], hour=tools.cutoff.hour,
                    weekday=tools.cutoff.weekday(), channel=target["channel"], mcc=target["mcc"])
    windows = {}
    for name, hours in WINDOWS.items():
        begin = tools.cutoff - timedelta(hours=hours)
        windows[name] = [r for r in tools.prior if local_time(r["event_time"]) >= begin]
        complete[name] = begin >= tools.start and tools.manifest["full_source_scanned"] is True
        if complete[name]: observed["count_" + name] = len(windows[name])
        elif features["count_" + name] < len(windows[name]): raise ValueError("input_count_below_observed_history")
    for name in ("24h", "30d"):
        if complete[name]: observed["sum_signed_cents_" + name] = sum(r["amount_cents"] for r in windows[name])
    if complete["30d"]:
        observed["merchant_seen_30d"] = int(any(r["merchant_id"] == target["merchant_id"] for r in windows["30d"]))
        observed["has_history_30d"] = int(bool(windows["30d"]))
    elif windows["30d"]:
        observed["has_history_30d"] = 1
        if any(r["merchant_id"] == target["merchant_id"] for r in windows["30d"]): observed["merchant_seen_30d"] = 1
    if tools.prior and tools.manifest["full_source_scanned"] is True:
        previous = max(local_time(r["event_time"]) for r in tools.prior)
        observed["seconds_since_previous_in_snapshot"] = int((tools.cutoff - previous).total_seconds())
    # An empty last-30-day case cannot reveal an event older than 30 days or prove no lifetime history.
    gaps = [name for name in MODEL_COLUMNS if name not in observed]
    for name, expected in observed.items():
        if features[name] != expected: raise ValueError("model_input_differs_from_case:" + name)
    return tools, features, {
        "status": "all_model_columns_verified" if not gaps else "available_columns_verified_with_explicit_gaps",
        "verified_fields": sorted(observed), "unverified_fields": gaps, "observed_values": observed,
        "complete_within_source": complete,
        "previous_time_gap": "Previous event may predate this case; supplied frozen feature retained, not reconstructed." if "seconds_since_previous_in_snapshot" in gaps else None,
        "rule": "same user; window start inclusive, target time exclusive; target/equal-time peers/future excluded",
    }


def load_feature_input(features_manifest, case_manifest):
    """Read exactly one label-free frozen feature row, never attach the label database."""
    tools = _case(case_manifest)
    path = Path(features_manifest)
    manifest = _json_file(path)
    if manifest.get("status") != "features_built" or manifest.get("labels_read") is not False: raise ValueError("label_free_frozen_features_required")
    if manifest["facts_sha256"] != tools.manifest["source_facts_sha256"] or manifest["cohort_report_sha256"] != tools.manifest["source_report_sha256"]:
        raise ValueError("feature_source_provenance_differs")
    database = path.parent / "features.sqlite"
    if sha256_file(database) != manifest["features_sha256"]: raise ValueError("feature_database_changed")
    names = ("transaction_id", "user_id", "event_time", *MODEL_COLUMNS)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        # The query cannot inspect another user's or a later/equal-time peer's feature row.
        rows = db.execute(f"SELECT {','.join(names)} FROM features WHERE transaction_id=? AND user_id=? AND event_time=? LIMIT 2",
                          (tools.target["transaction_id"], tools.manifest["user_id"], tools.cutoff.isoformat())).fetchall()
    if len(rows) != 1: raise ValueError("bound_feature_row_not_unique_or_absent")
    row = dict(rows[0])
    bound = {name: row[name] for name in names[:3]} | {"features": {name: row[name] for name in MODEL_COLUMNS}}
    validate_input(case_manifest, bound)
    if sha256_file(database) != manifest["features_sha256"]: raise ValueError("feature_database_changed_during_read")
    return bound, {"features_manifest_sha256": sha256_file(path), "features_database_sha256": manifest["features_sha256"],
                   "feature_record_sha256": digest(bound), "labels_read": False, "query_scope": "one bound transaction/user/time"}


def perturbations(tools, features, validation):
    complete = validation["complete_within_source"]
    begin = tools.cutoff - timedelta(hours=720)
    positives = sorted(r["amount_cents"] for r in tools.prior if r["amount_cents"] > 0 and local_time(r["event_time"]) >= begin)
    specs = [
        ("amount_to_prior_positive_lower_median_30d", "amount_cents", positives[(len(positives) - 1)//2] if positives else None,
         None if complete["30d"] and positives and features["amount_cents"] > 0 else "complete_30d_positive_history_and_positive_target_required",
         "Replace this amount with the lower median observed positive amount in the prior 30d; integer source cents."),
        ("remove_hour_count_input_only", "count_1h", 0,
         None if complete["1h"] else "complete_1h_history_required",
         "Set only count_1h to zero; other counts/sums/previous-event gap remain fixed and may be inconsistent with real history."),
        ("mark_merchant_seen_input_only", "merchant_seen_30d", 1,
         None if complete["30d"] and features["has_history_30d"] else "complete_nonempty_30d_history_required",
         "Set only merchant_seen_30d to one; no historical record is created and no claim the merchant actually appeared."),
    ]
    result = []
    for identity, column, replacement, reason, interpretation in specs:
        if reason is None and features[column] == replacement: reason = "no_change_at_this_input"
        row = {"id": identity, "status": "skipped" if reason else "ready", "skip_reason": reason,
               "changed_column": column, "before": features[column], "after": replacement,
               "interpretation": interpretation, "causal": False}
        result.append(row)
    return result


def explain_case(manifest_path, bound_input, scorer, *, model_metadata=None, input_provenance=None):
    """scorer.score_many(list[exact feature dict]) -> list[list[member sigmoid scores]]."""
    tools, features, validation = validate_input(manifest_path, bound_input)
    changes = perturbations(tools, features, validation)
    inputs = [features]
    for change in changes:
        if change["status"] == "ready": inputs.append(features | {change["changed_column"]: change["after"]})
    values = scorer.score_many(deepcopy(inputs))
    if not isinstance(values, (list, tuple)) or len(values) != len(inputs): raise ValueError("score_rows_differ")
    checked = []
    for scores in values:
        if not isinstance(scores, (list, tuple)) or not 1 <= len(scores) <= 3: raise ValueError("one_to_three_member_scores_required")
        if any(isinstance(s, bool) or not isinstance(s, Real) or not math.isfinite(s) or not 0 <= s <= 1 for s in scores): raise ValueError("finite_sigmoid_scores_required")
        checked.append([float(s) for s in scores])
    if len({len(scores) for scores in checked}) != 1: raise ValueError("ensemble_member_count_changed")
    if model_metadata and "model_count" in model_metadata and model_metadata["model_count"] != len(checked[0]):
        raise ValueError("model_metadata_member_count_differs")
    original = sum(checked[0]) / len(checked[0])
    position = 1
    for change in changes:
        if change["status"] != "ready": continue
        scores = checked[position]; position += 1
        change.update(status="scored", member_scores=scores, score=sum(scores)/len(scores),
                      delta=sum(scores)/len(scores) - original,
                      member_deltas=[new-old for new, old in zip(scores, checked[0])],
                      input_sha256=digest(inputs[position-1]))
    result = {"version": VERSION, "status": "sensitivity_computed", "data_kind": tools.manifest["data_kind"],
              "transaction_id": tools.target["transaction_id"], "user_id": tools.manifest["user_id"], "cutoff": tools.cutoff.isoformat(),
              "original": {"score": original, "member_scores": checked[0], "input_sha256": digest(features)},
              "source_alert_score": tools.manifest["alert"].get("score"), "same_as_alert_model_verified": False,
              "perturbations": changes, "input_validation": validation, "input_provenance": input_provenance,
              "model": model_metadata or {"kind": "caller_supplied_scorer", "model_artifacts_verified": False},
              "projection_sha256": tools.manifest["projection_sha256"], "case_manifest_sha256": sha256_file(manifest_path),
              "bound_input_sha256": digest(bound_input), "model_columns": list(MODEL_COLUMNS),
              "score_kind": "uncalibrated_model_ranking_sigmoid", "aggregation": "arithmetic_mean_across_members",
              "causal": False, "fraud_verdict": None, "labels_read": False, "policy_approved": False, "deployed": False,
              "limitations": CAUTIONS}
    result["evidence_id"] = "sensitivity-" + digest(result)[:24]
    return result


class FrozenNeuralEnsemble:
    """Explicitly trusted local pickle + SHA256 checks; hashes do not make foreign pickle safe."""
    def __init__(self, models, *, trusted_local=False):
        if trusted_local is not True: raise ValueError("explicit_trusted_local_models_required")
        if not isinstance(models, list) or not 1 <= len(models) <= 3: raise ValueError("one_to_three_frozen_models_required")
        from app.risk.neural_v2 import load_v2
        self.predictors, descriptions, directories = [], [], set()
        for model in models:
            if not isinstance(model, dict) or set(model) != {"directory", "artifact_sha256"}: raise ValueError("model_manifest_required")
            directory, hashes = Path(model["directory"]).resolve(), model["artifact_sha256"]
            if directory in directories: raise ValueError("distinct_frozen_models_required")
            directories.add(directory)
            if not isinstance(hashes, dict) or set(hashes) != {"model.pt", "preprocessor.joblib"}: raise ValueError("both_model_hashes_required")
            for name, expected in hashes.items():
                if not isinstance(expected, str) or len(expected) != 64 or sha256_file(directory/name) != expected: raise ValueError("frozen_model_changed")
            self.predictors.append(load_v2(directory))
            if any(sha256_file(directory/name) != expected for name, expected in hashes.items()): raise ValueError("model_changed_during_load")
            descriptions.append({"directory": str(directory), "artifact_sha256": hashes})
        self.metadata = {"kind": "trusted_local_frozen_neural_v2", "model_artifacts_verified": True,
                         "members": descriptions, "model_count": len(self.predictors)}

    def score_many(self, rows):
        import pandas as pd
        frame = pd.DataFrame(rows, columns=list(MODEL_COLUMNS))
        predictions = [predictor.predict(frame) for predictor in self.predictors]
        return [[float(score[i]) for score in predictions] for i in range(len(frame))]


def models_from_neural_run(directory, *, seeds=(42, 43, 44)):
    """Use saved run report hashes, never invent trust by hashing arbitrary files on load."""
    if not isinstance(seeds, (tuple, list)) or not 1 <= len(seeds) <= 3 or any(type(s) is not int or not 0 <= s < 2**32 for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError("one_to_three_distinct_integer_seeds_required")
    directory = Path(directory).resolve()
    report = _json_file(directory / "report.json", maximum=2000000)
    hashes = report["artifact_sha256"]
    models = []
    for seed in seeds:
        relative = f"confirmation-seed-{seed}/model"
        model = (directory / relative).resolve()
        if not model.is_relative_to(directory): raise ValueError("model_outside_frozen_run")
        models.append({"directory": str(model), "artifact_sha256": {name: hashes[relative + "/" + name] for name in ("model.pt", "preprocessor.joblib")}})
    return models


def render_explanation(result):
    display = {"amount_cents": "本笔金额（0.01源单位）", "count_1h": "此前1小时笔数", "merchant_seen_30d": "30天内见过商户（0否/1是）"}
    lines = ["# 深度预警分数：单输入敏感性", "", "本页不判断欺诈，也不解释真实因果。", "",
             f"绑定交易：{result['transaction_id']}；截止：{result['cutoff']}；证据：{result['evidence_id']}。",
             f"该冻结神经模型对原始输入的分数：{result['original']['score']:.8f}（未校准sigmoid分数，不是欺诈概率）。",
             f"案例原预警分数：{result['source_alert_score']}。本页未核实原预警是否来自相同检测器，不能把这两个分数的差当作特征解释。", "",
             "| 输入扰动 | 原值 → 试验值 | 新分数 | 分差 |", "|---|---|---:|---:|"]
    for row in result["perturbations"]:
        if row["status"] == "scored": lines.append(f"| {display[row['changed_column']]} | {row['before']} → {row['after']} | {row['score']:.8f} | {row['delta']:+.8f} |")
        else: lines.append(f"| {display[row['changed_column']]} | 未执行：{row['skip_reason']} | — | — |")
    lines.extend(["", "每次仅改一个原始模型输入；模型内部衍生特征会重新计算。计数或新商户标志的单独改动，可能与其他固定输入不一致，不能当成可实现的交易历史。分差不能相加当成特征贡献；不能预设减少频次就一定降分。",
                  "", "快照未能核验的输入：" + ("、".join(result["input_validation"]["unverified_fields"]) or "无"),
                  "空30天历史不能证明全生命周期没有上一次交易；若最近间隔来自更早数据，必须保留冻结输入并说明缺口。",
                  "金额均为0.01源单位，币种未知；源内覆盖不等于真实业务完整。未读取欺诈标签，没有金融批准或部署。"])
    return "\n".join(lines) + "\n"
