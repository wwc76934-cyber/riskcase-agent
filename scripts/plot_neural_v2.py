"""Standalone training/ablation figure from immutable development receipts."""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "tmp/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.directory / "report.json").read_text(encoding="utf-8"))
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6), layout="constrained")
    for recipe in report["recipes"]:
        curves = [json.loads(line) for line in (args.directory / recipe["recipe"] / "curves.jsonl").read_text(encoding="utf-8").splitlines()]
        label = recipe["recipe"].replace("residual_", "Res ").replace("_", " ")
        axes[0].plot([r["epoch"] for r in curves], [r["true_positives"] for r in curves], marker=".", label=label)
        axes[1].plot([r["epoch"] for r in curves], [r["average_precision"] for r in curves], marker=".")
    axes[0].axhline(95, color="#555555", ls="--", lw=1, label="Original HGB (95)")
    axes[0].axhline(42, color="#999999", ls=":", lw=1, label="Original MLP (42)")
    axes[0].set(title="2018 development: fixed daily top 10", xlabel="Training epoch", ylabel="Captured source fraud labels")
    axes[0].legend(loc="upper left", fontsize=7)
    axes[1].set(title="AP during development search", xlabel="Training epoch", ylabel="Average precision")
    names = [str(r["seed"]) for r in report["seeds"]] + ["Mean scores"]
    values = [r["metrics"]["daily_review"]["true_positives"] for r in report["seeds"]] + [report["ensemble"]["daily_review"]["true_positives"]]
    axes[2].bar(names, values, color=["#669bbc"] * 3 + ["#1d6a96"])
    axes[2].bar_label(axes[2].containers[0], padding=3)
    axes[2].set_ylim(0, max(values) * 1.17)
    axes[2].set(title="Selected recipe: seed variation", xlabel="Training seed / ensemble", ylabel="Captured source fraud labels")
    fig.suptitle("Residual neural detector — synthetic, observed development data; 2019 excluded from this experiment", fontsize=12)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)


if __name__ == "__main__": main()
