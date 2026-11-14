"""List cases where the LLM and the truth disagree (for prompt development on dev only).

    python -m scripts.semantic_errors <data.jsonl> <preds.jsonl>
"""
import sys

from app.semantic.benchmark import read_jsonl
from app.semantic.policy import decide, is_intercept

sys.stdout.reconfigure(encoding="utf-8")
cases = {c["id"]: c for c in read_jsonl(sys.argv[1])}
preds = {r["id"]: r["result"] for r in read_jsonl(sys.argv[2])}
tp = fp = fn = tn = 0
for cid, res in preds.items():
    c = cases[cid]
    names = [s["name"] for s in res.get("signals", [])]
    d = decide(names)
    model_f, pol_f = res.get("is_fraud"), is_intercept(d)
    truth = c["label"] == "fraud"
    for tag, pred in (("model", model_f), ("policy", pol_f)):
        if pred != truth:
            print(f"{tag:<7}{'FN' if truth else 'FP'} {cid} truth_signals={c['truth_signals']} got={names} type={res.get('fraud_type')} :: {res.get('reasoning','')[:70]}")
    tp += model_f and truth; fp += model_f and not truth; fn += (not model_f) and truth; tn += (not model_f) and not truth
print("model: tp", tp, "fp", fp, "fn", fn, "tn", tn)
