"""Plot recorded training curves; plotting can use a separate matplotlib runtime."""

import argparse
import json
import os
from pathlib import Path

cache = Path(__file__).resolve().parents[1] / "tmp" / "matplotlib"
cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(cache))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    run = args.report.parent
    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run / "curves.jsonl").read_text(encoding="utf-8").splitlines()]
    selection = [row for row in rows if row["phase"] == "selection"]
    refit = [row for row in rows if row["phase"] == "refit"]
    epochs = [row["epoch"] for row in selection]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.3), constrained_layout=True)
    axes[0].plot(epochs, [row["train_eval_weighted_loss"] for row in selection], "o-", label="Train, eval mode")
    axes[0].plot(epochs, [row["selection_weighted_loss"] for row in selection], "s-", label="2017 H2 internal validation")
    axes[0].set(title="Epoch selection: weighted loss", xlabel="Epoch", ylabel="Loss")
    axes[0].legend(fontsize=8)
    axes[1].plot(epochs, [row["train_AP"] for row in selection], "o-", label="Train AP")
    axes[1].plot(epochs, [row["selection_AP"] for row in selection], "s-", label="Internal validation AP")
    axes[1].axvline(report["selection"]["selected_epoch"], ls="--", color="gray", label="Selected epoch")
    axes[1].set(title="Ranking quality (AP)", xlabel="Epoch", ylabel="AP (log scale)")
    axes[1].set_yscale("log")
    axes[1].legend(fontsize=8)
    axes[2].plot([row["epoch"] for row in refit], [row["training_updates"]["weighted_loss"] for row in refit], "o-", color="#118577")
    axes[2].set(title="Refit from scratch: update loss", xlabel="Epoch", ylabel="Weighted loss (with dropout)")
    for axis in axes:
        axis.grid(alpha=.2)
        axis.xaxis.set_major_locator(MaxNLocator(integer=True))
    fig.suptitle("RiskCase MLP v1 | Synthetic data | 2018 scores never used for epoch selection", fontsize=11)
    fig.savefig(run / "training-curves.png", dpi=160)
    plt.close(fig)
    print(run / "training-curves.png")


if __name__ == "__main__":
    main()
