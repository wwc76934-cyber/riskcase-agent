"""Plot the already frozen final report; never train, infer or open outcome records."""

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "tmp/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


LABELS = {"amount_only": "Amount baseline", "logistic_v1": "Original logistic", "hgb_v1": "Original HGB",
          "hgb_v2": "Matched HGB", "hgb_engineered": "Engineered HGB", "mlp_v1": "Original MLP",
          "neural_seed42": "Neural seed 42", "neural_seed43": "Neural seed 43", "neural_seed44": "Neural seed 44",
          "neural_v2_ensemble": "PRIMARY: neural mean"}


def read_checked(directory):
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    audit = json.loads((directory / "independent-audit.json").read_text(encoding="utf-8"))
    if audit["report_sha256"] != digest(directory / "report.json") or audit["status"] != "independent_metrics_and_review_membership_verified": raise ValueError("Final audit/report mismatch")
    for key, name in (("contract_sha256", "contract.json"), ("predictions_manifest_sha256", "predictions.json"), ("outcomes_opened_sha256", "outcomes-opened.json")):
        if report[key] != digest(directory / name): raise ValueError("Final receipt changed")
    if report["primary_model_frozen_before_test"] != "neural_v2_ensemble" or report["selection_after_final_test"] or report["primary_budget"] != 10: raise ValueError("Unexpected final selection")
    return report


def plot(directory, output):
    report = read_checked(directory)
    stats = report["strategies"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(18, 6.2), gridspec_kw={"width_ratios": [1.4, 1.15, 1.15]}, layout="constrained")
    order = list(stats)
    positions = np.arange(len(order))
    values = [stats[name]["daily_review"]["10"]["true_positives"] for name in order]
    colors = ["#126b62" if name == "neural_v2_ensemble" else "#487e96" if name == "hgb_v2" else "#98afba" for name in order]
    axes[0].barh(positions, values, color=colors, height=.68)
    axes[0].set_yticks(positions, [LABELS[name] for name in order])
    axes[0].invert_yaxis()
    for y, name, value in zip(positions, order, values):
        axes[0].text(value + 2, y, f"{value}  |  AP {stats[name]['average_precision']:.4f}", va="center", fontsize=8.5)
    axes[0].set(xlim=(0, 163), xlabel="Captured source labels (bar); AP at right", title="All 10 frozen strategies | daily budget 10")
    budgets = [5, 10, 20]
    for name, color in (("mlp_v1", "#b0b8bd"), ("hgb_v1", "#7c8f98"), ("hgb_v2", "#487e96"), ("neural_v2_ensemble", "#126b62")):
        captured = [stats[name]["daily_review"][str(b)]["true_positives"] for b in budgets]
        axes[1].plot(budgets, captured, marker="o", color=color, label=LABELS[name], lw=2 if name == "neural_v2_ensemble" else 1.5)
        if name == "neural_v2_ensemble":
            for x, value in zip(budgets, captured): axes[1].annotate(str(value), (x, value), xytext=(0, 8), textcoords="offset points", ha="center", color=color, fontsize=9)
    axes[1].axvline(10, color="#9fa7aa", ls=":", lw=1)
    axes[1].set(xticks=budgets, ylim=(0, 185), xlabel="Reviews per day (365 days)", ylabel="Captured source labels", title="Capacity sensitivity | primary budget stays 10")
    axes[1].legend(loc="upper left", fontsize=8)
    comparisons = report["paired_comparison"]["neural_v2_ensemble"]
    for y, name in enumerate(("hgb_v1", "hgb_v2")):
        row = comparisons[name]; delta = row["paired_net_tp"]
        lower, upper = row["day_bootstrap_interval_95"]
        axes[2].errorbar(delta, y, xerr=[[delta - lower], [upper - delta]], fmt="o", color="#126b62", capsize=5, lw=2)
        axes[2].text(delta, y + .18, f"{delta:+d}  [{lower:.0f}, {upper:.0f}]", ha="center", fontsize=10)
    axes[2].axvline(0, color="#777777", ls="--", lw=1)
    axes[2].set(yticks=[0, 1], yticklabels=["vs original HGB", "vs matched HGB"], ylim=(-.5, 1.55), xlim=(-25, 85), xlabel="Net captured labels | 95% day-resampling interval", title="Primary ensemble | paired sensitivity")
    axes[2].text(.03, .04, "Matched-tree interval crosses zero.\nNot evidence of a stable advantage.\n2,000 paired-day resamples;\nlonger/user dependence not modeled.", transform=axes[2].transAxes, fontsize=9, color="#495d64", va="bottom", bbox={"facecolor": "white", "edgecolor": "none", "alpha": .95, "pad": 3})
    fig.suptitle("2019 time holdout: synthetic transactions | all weights trained on 2016–2017", fontsize=13)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "runs/final-detector-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/final-detector-results.png")
    args = parser.parse_args()
    plot(args.directory, args.output)


if __name__ == "__main__": main()
