"""Small interpretable detection baseline and label-isolated evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import heapq
import math
from numbers import Real


@dataclass(frozen=True)
class RuleThresholds:
    large_positive_amount_cents: int
    high_prior_count_1h: int
    minimum_combined_hits: int = 2

    def __post_init__(self):
        if type(self.large_positive_amount_cents) is not int or self.large_positive_amount_cents <= 0:
            raise ValueError("Positive integer amount threshold required")
        if type(self.high_prior_count_1h) is not int or self.high_prior_count_1h < 0:
            raise ValueError("Nonnegative integer count threshold required")
        if type(self.minimum_combined_hits) is not int or not 1 <= self.minimum_combined_hits <= 3:
            raise ValueError("Combined rule needs 1..3 hits")


def fit_thresholds(connection, *, percentile=99) -> RuleThresholds:
    """Training FEATURES only. Never accesses a label table or validation rows."""
    if type(percentile) is not int or not 1 <= percentile <= 100:
        raise ValueError("Percentile must be an integer in 1..100")
    values = []
    for column, condition in (("amount_cents", "AND amount_cents>0"), ("count_1h", "")):
        count = connection.execute(f"SELECT COUNT(*) FROM features WHERE split='train' {condition}").fetchone()[0]
        if not count:
            raise ValueError("Training rows/positive amounts required")
        offset = max(0, (count * percentile + 99) // 100 - 1)
        value = connection.execute(f"SELECT {column} FROM features WHERE split='train' {condition} ORDER BY {column} LIMIT 1 OFFSET ?", (offset,)).fetchone()[0]
        values.append(value)
    return RuleThresholds(*values)


def score_transaction(row, thresholds: RuleThresholds):
    for name in ("amount_cents", "count_1h", "has_history_30d", "merchant_seen_30d"):
        if type(row[name]) is not int:
            raise ValueError(f"Integer feature required: {name}")
    if row["count_1h"] < 0 or row["has_history_30d"] not in (0, 1) or row["merchant_seen_30d"] not in (0, 1):
        raise ValueError("Invalid history count/flags")
    if row["merchant_seen_30d"] and not row["has_history_30d"]:
        raise ValueError("Merchant cannot have been seen without history")
    reasons = []
    if row["amount_cents"] > thresholds.large_positive_amount_cents:
        reasons.append("LARGE_POSITIVE_AMOUNT")
    if row["count_1h"] > thresholds.high_prior_count_1h:
        reasons.append("HIGH_PRIOR_COUNT_1H")
    if row["has_history_30d"] and not row["merchant_seen_30d"]:
        reasons.append("MERCHANT_ID_NOT_SEEN_30D")
    return {
        "amount_only": {"score": max(0, row["amount_cents"]),
                        "alert": "LARGE_POSITIVE_AMOUNT" in reasons,
                        "reasons": [code for code in reasons if code == "LARGE_POSITIVE_AMOUNT"]},
        "history_rules": {"score": len(reasons), "alert": len(reasons) >= thresholds.minimum_combined_hits,
                          "reasons": reasons},
    }


def confusion_metrics(*, rows, positives, alerts, true_positives):
    if any(type(n) is not int or n < 0 for n in (rows, positives, alerts, true_positives)):
        raise ValueError("Nonnegative integer counts required")
    tp = true_positives
    fp, fn = alerts - tp, positives - tp
    tn = rows - tp - fp - fn
    if min(tp, fp, fn, tn) < 0:
        raise ValueError("Inconsistent confusion counts")
    return {"rows": rows, "positives": positives, "reviewed_or_alerted": alerts,
            "true_positives": tp, "false_positives": fp, "false_negatives": fn, "true_negatives": tn,
            "precision": tp / alerts if alerts else None, "recall": tp / positives if positives else None,
            "false_positive_rate": fp / (rows - positives) if rows > positives else None}


class DailyReviewer:
    """End-of-day batch review allocation, never a real-time blocking policy."""
    def __init__(self, budget=10, seed="riskcase-review-ties-v1"):
        if type(budget) is not int or budget < 1 or not isinstance(seed, str) or not seed:
            raise ValueError("Positive daily review budget and seed required")
        self.budget, self.seed = budget, seed
        self.heaps = defaultdict(list)
        self.rows = self.positives = 0

    def add(self, *, transaction_id, event_time, score, is_fraud, reasons, threshold_alert):
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score) or type(is_fraud) is not bool:
            raise ValueError("Finite numeric score and boolean outcome required")
        if threshold_alert is not None and type(threshold_alert) is not bool:
            raise ValueError("Threshold status must be bool or unknown")
        self.rows += 1
        self.positives += int(is_fraud)
        day = event_time[:10]
        tie = hashlib.sha256(f"{self.seed}\0{transaction_id}".encode()).hexdigest()
        # Ranking keys exclude labels. Physical transaction IDs are unique.
        item = (score, tie, transaction_id, {"transaction_id": transaction_id, "event_time": event_time,
                "score": score, "reason_codes": list(reasons), "threshold_alert": threshold_alert}, is_fraud)
        heap = self.heaps[day]
        if len(heap) < self.budget:
            heapq.heappush(heap, item)
        elif item[:3] > heap[0][:3]:
            heapq.heapreplace(heap, item)

    def finish(self):
        queue, caught = [], 0
        for day in sorted(self.heaps):
            for rank, item in enumerate(sorted(self.heaps[day], key=lambda value: value[:3], reverse=True), start=1):
                queue.append({**item[3], "day_rank": rank})  # NO labels exported
                caught += int(item[4])
        metrics = confusion_metrics(rows=self.rows, positives=self.positives, alerts=len(queue), true_positives=caught)
        metrics.update(observed_days=len(self.heaps), daily_budget=self.budget,
                       reviewed_without_threshold_hit=None if any(row["threshold_alert"] is None for row in queue)
                       else sum(not row["threshold_alert"] for row in queue))
        return metrics, queue


def evaluate_validation(rows, thresholds: RuleThresholds, *, daily_budget=10, tie_seed="riskcase-review-ties-v1"):
    """Caller supplies only joined validation records. Reject other partitions."""
    reviewers = {name: DailyReviewer(daily_budget, tie_seed) for name in ("amount_only", "history_rules")}
    counts = {name: Counter() for name in reviewers}
    for row in rows:
        if row["split"] != "validation":
            raise ValueError("This stage may evaluate validation only; final test stays sealed")
        if type(row["is_fraud"]) is not bool:
            raise ValueError("Boolean evaluation label required")
        scored = score_transaction(row, thresholds)
        for name, output in scored.items():
            counts[name]["rows"] += 1
            counts[name]["positives"] += int(row["is_fraud"])
            counts[name]["alerts"] += int(output["alert"])
            counts[name]["true_positives"] += int(output["alert"] and row["is_fraud"])
            reviewers[name].add(transaction_id=row["transaction_id"], event_time=row["event_time"],
                score=output["score"], is_fraud=row["is_fraud"], reasons=output["reasons"], threshold_alert=output["alert"])
    if not counts["amount_only"]["rows"]:
        raise ValueError("Validation records required")
    metrics, queues = {}, {}
    for name in reviewers:
        metrics[name] = {"threshold_policy": confusion_metrics(**{key: counts[name][key] for key in (
            "rows", "positives", "alerts", "true_positives")})}
        metrics[name]["daily_review"], queue = reviewers[name].finish()
        queues[name] = [{"strategy": name, **row} for row in queue]
    return {"thresholds": asdict(thresholds), "strategies": metrics}, queues
