"""Metrics for the semantic fraud benchmark, with bootstrap intervals over cases."""
import random


def rates(rows):
    """rows: dicts with label, kind, fraud_type (truth) and pred_fraud, pred_type."""
    fraud = [r for r in rows if r["label"] == "fraud"]
    legit = [r for r in rows if r["label"] == "legit"]
    near = [r for r in rows if r["kind"] == "near"]
    ordinary = [r for r in rows if r["kind"] == "ordinary"]
    tp = sum(r["pred_fraud"] for r in fraud)
    fp = sum(r["pred_fraud"] for r in legit)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(fraud) if fraud else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(
        n=len(rows), fraud_recall=recall, precision=precision, f1=f1,
        fpr_near=(sum(r["pred_fraud"] for r in near) / len(near)) if near else None,
        fpr_ordinary=(sum(r["pred_fraud"] for r in ordinary) / len(ordinary)) if ordinary else None,
        type_acc=(sum(1 for r in fraud if r["pred_fraud"] and r["pred_type"] == r["fraud_type"]) / len(fraud)) if fraud else None)


def bootstrap(rows, key, *, reps=1000, seed=0):
    rng = random.Random(seed)
    values = []
    for _ in range(reps):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        value = rates(sample)[key]
        if value is not None:
            values.append(value)
    values.sort()
    if not values:
        return None
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def summarise(rows):
    out = rates(rows)
    for key in ("f1", "fpr_near", "fraud_recall"):
        out[key + "_ci"] = bootstrap(rows, key)
    return out
