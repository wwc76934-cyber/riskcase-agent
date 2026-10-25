"""Publish compact, auditable neural experiment statistics, not private source rows."""

import json
from pathlib import Path

from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / "runs/neural-v2-development-01"
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    verified = json.loads((directory / "verification.json").read_text(encoding="utf-8"))
    if verified["status"] != "verified" or verified["report_sha256"] != sha256_file(directory / "report.json"): raise ValueError("Verify current run first")
    def compact(metrics):
        return {"AP": metrics["average_precision"], "ROC_AUC": metrics["roc_auc"], "daily_review": metrics["daily_review"], "channel_groups": metrics["error_groups"]["channel"]}
    controls = {}
    for name in ("hgb-v2-development-01", "hgb-raw-nochannel-v2-development-01"):
        path = root / "runs" / name / "report.json"
        tree = json.loads(path.read_text(encoding="utf-8"))
        controls[name] = {"report_sha256": sha256_file(path), "final_test_read": tree["final_test_read"], "metrics": compact(tree["metrics"]), "reload_max_diff": tree["reload_max_diff"], "elapsed_seconds": tree["elapsed_seconds"]}
    result = {"date": "2026-10-22", "status": "neural_development_evaluated_and_verified", "data_kind": "IBM_TabFormer_synthetic",
              "fit_rows": report["fit_rows"], "fit_positives": report["fit_positives"], "development_rows": report["development_rows"], "development_positives": report["development_positives"],
              "fit_period": "2016-2017", "evaluation_period": "2018 previously observed development", "final_test_read": False,
              "run_report_sha256": sha256_file(directory / "report.json"), "protocol_sha256": sha256_file(directory / "protocol.json"), "verification_sha256": sha256_file(directory / "verification.json"),
              "selected": report["selected"], "recipes": [{"id": r["recipe"], "config": r["config"], "selected_epoch": r["best"]["epoch"], "TP10": r["best"]["true_positives"], "AP": r["best"]["average_precision"], "epochs_attempted": r["epochs_attempted"], "parameter_count": r["parameter_count"], "reload_max_diff": r["reload_max_abs_diff"], "seconds": r["elapsed_seconds"]} for r in report["recipes"]],
              "seeds": [{"seed": r["seed"], "metrics": compact(r["metrics"]), "seconds": r["elapsed_seconds"]} for r in report["seeds"]],
              "ensemble": compact(report["ensemble"]), "matched_controls": controls,
              "seed_TP10_mean": verified["seed_TP10_mean"], "seed_TP10_sample_std": verified["seed_TP10_sample_std"], "elapsed_seconds": report["elapsed_seconds"],
              "final_contract_revision": "Initial optional refit-through-2018 plan superseded before holdout access: keep 2016-2017 weights for all controls and three-seed equal ensemble; root owns independent one-time 2019 evaluation.",
              "limitations": report["limits"]}
    write_json(root / "docs/neural-v2-results.json", result)
    print(json.dumps({k: result[k] for k in ("selected", "seed_TP10_mean", "seed_TP10_sample_std", "ensemble", "elapsed_seconds")}, indent=2))


if __name__ == "__main__": main()
