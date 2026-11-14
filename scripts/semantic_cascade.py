"""Cascade analysis from saved predictions: small model first, escalate to the large model when unsure.

    python -m scripts.semantic_cascade <set> <variant> [confidence_threshold]

No new model calls. Escalate when the small model's reply is unreadable, its confidence is below the
threshold, or its own is_fraud field disagrees with the host policy over its checked signals. Cost
uses the recorded per-call estimates: the escalated case pays for both calls.
"""
from decimal import Decimal
import sys

from app.semantic.benchmark import read_jsonl
from app.semantic.evaluate import rates
from app.semantic.policy import decide, is_intercept

SETS = {"test": "data/semantic/test.ds.jsonl", "shift": "data/semantic/shift.q7.jsonl"}
set_name, variant = sys.argv[1], sys.argv[2]
threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.8
cases = {c["id"]: c for c in read_jsonl(SETS[set_name])}
small = {r["id"]: r["result"] for r in read_jsonl(f"runs/semantic/{set_name}-{variant}-q7.jsonl")}
large = {r["id"]: r["result"] for r in read_jsonl(f"runs/semantic/{set_name}-{variant}-qw.jsonl")}
ids = [i for i in cases if i in small and i in large]


def verdict(res):
    if not res["ok"]:
        return False
    return is_intercept(decide([s["name"] for s in res["signals"]]))


def cost(res):
    return Decimal(res["meta"]["cny"])


def unsure(res):
    if not res["ok"] or res["confidence"] < threshold:
        return True
    return res["is_fraud"] != verdict(res)


rows = {"small_only": [], "large_only": [], "cascade": []}
spent = {k: Decimal(0) for k in rows}
escalated = 0
for i in ids:
    c = cases[i]
    s, l = small[i], large[i]
    spent["small_only"] += cost(s)
    spent["large_only"] += cost(l)
    rows["small_only"].append(dict(c, pred_fraud=verdict(s), pred_type="none"))
    rows["large_only"].append(dict(c, pred_fraud=verdict(l), pred_type="none"))
    spent["cascade"] += cost(s)
    if unsure(s):
        escalated += 1
        spent["cascade"] += cost(l)
        rows["cascade"].append(dict(c, pred_fraud=verdict(l), pred_type="none"))
    else:
        rows["cascade"].append(dict(c, pred_fraud=verdict(s), pred_type="none"))
print(f"{set_name}/{variant} n={len(ids)} escalation threshold {threshold} escalated {escalated} ({escalated / len(ids):.0%})")
print(f"{'strategy':<12}{'F1':>7}{'recall':>8}{'FPR_near':>10}{'cost CNY':>10}")
for k, v in rows.items():
    r = rates(v)
    print(f"{k:<12}{r['f1']:>7.3f}{r['fraud_recall']:>8.3f}{r['fpr_near']:>10.3f}{float(spent[k]):>10.3f}")
