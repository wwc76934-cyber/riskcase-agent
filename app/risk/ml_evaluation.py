"""Ranking metrics and budget-identical review; no fitted transforms here."""

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from app.risk.rule_baseline import DailyReviewer, confusion_metrics


def evaluate_scores(metadata, y, scores, *, daily_budget=10, tie_seed="riskcase-review-ties-v1", expected_split="validation"):
    y, scores = np.asarray(y), np.asarray(scores, dtype=np.float64)
    if len(metadata) != len(y) or len(y) != len(scores) or not len(y):
        raise ValueError("Scores/outcomes/metadata length mismatch")
    if not np.isin(y, [0, 1]).all() or not np.isfinite(scores).all():
        raise ValueError("Binary outcomes and finite scores required")
    if expected_split not in {"train", "validation"} or not metadata["split"].eq(expected_split).all():
        raise ValueError("Evaluation requires the declared development split; final test is forbidden")
    reviewer = DailyReviewer(daily_budget, tie_seed)
    for identity, stamp, outcome, score in zip(metadata["transaction_id"], metadata["event_time"], y, scores):
        reviewer.add(transaction_id=identity, event_time=stamp, score=float(score), is_fraud=bool(outcome),
                     reasons=["RANKING_SCORE_ONLY"], threshold_alert=None)
    daily, queue = reviewer.finish()
    return {"average_precision": float(average_precision_score(y, scores)) if y.sum() else None,
            "roc_auc": float(roc_auc_score(y, scores)) if len(np.unique(y)) == 2 else None,
            "positive_fraction": float(y.mean()), "daily_review": daily}, queue


def error_groups(metadata, X, y, queue, *, train_users):
    selected = metadata["transaction_id"].isin({row["transaction_id"] for row in queue}).to_numpy()
    groups = {
        "channel": X["channel"].astype(str).to_numpy(),
        "observed_in_training": np.where(metadata["user_id"].isin(train_users), "seen", "unseen"),
        "prior_30d_events": np.where(X["has_history_30d"].to_numpy() == 1, "present", "none"),
    }
    report = {}
    for grouping, values in groups.items():
        report[grouping] = {}
        for value in sorted(set(values)):
            mask = values == value
            report[grouping][value] = confusion_metrics(rows=int(mask.sum()), positives=int(y[mask].sum()),
                alerts=int(selected[mask].sum()), true_positives=int((selected[mask] & (y[mask] == 1)).sum()))
    return report
