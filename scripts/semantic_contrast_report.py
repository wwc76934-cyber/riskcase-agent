"""Score all methods on the hand-written contrast set, with pair-level accuracy.

    python -m scripts.semantic_contrast_report [out.json] [--tags qw q7 ...]
"""
import argparse
import json
from pathlib import Path

from app.semantic.baselines import MARKERS, TfidfLR
from app.semantic.benchmark import read_jsonl
from app.semantic.evaluate import rates
from app.semantic.policy import decide, is_intercept

parser = argparse.ArgumentParser()
parser.add_argument("out", nargs="?", type=Path)
parser.add_argument("--tags", nargs="+", default=["qw", "q7"])
parser.add_argument("--threshold", type=int, default=2)
parser.add_argument("--data", default="data/semantic/contrast.human.jsonl")
parser.add_argument("--prefix", default="contrast")
a = parser.parse_args()

rows = read_jsonl(a.data)
train = read_jsonl("data/semantic/train.ds.jsonl")
tf = TfidfLR().fit([c["text"] for c in train], [c["label"] for c in train], [c["fraud_type"] for c in train])
methods = {
    "rules": [sum(m in r["text"] for m in MARKERS) >= a.threshold for r in rows],
    "tfidf_lr": [p["is_fraud"] for p in tf.predict([r["text"] for r in rows])],
}
details = {}
for tag in a.tags:
    preds = {x["id"]: x["result"] for x in read_jsonl(f"runs/semantic/{a.prefix}-clean-{tag}.jsonl")}
    if len(preds) != len(rows):
        continue
    methods[f"llm_{tag}_model"] = [bool(preds[r["id"]].get("is_fraud")) for r in rows]
    methods[f"llm_{tag}_policy"] = [is_intercept(decide([s["name"] for s in preds[r["id"]].get("signals", [])])) for r in rows]
    details[tag] = preds

report = {}
print(f"{'method':<18}{'recall':>8}{'FPR':>7}{'F1':>7}{'pairs right':>13}")
for name, preds in methods.items():
    r = rates([dict(x, pred_fraud=p, pred_type="none") for x, p in zip(rows, preds)])
    pairs = sum(1 for i in range(0, len(rows), 2) if preds[i] and not preds[i + 1])
    report[name] = dict(recall=r["fraud_recall"], fpr=r["fpr_near"], f1=r["f1"], pairs_right=pairs, pairs=len(rows) // 2)
    print(f"{name:<18}{r['fraud_recall']:>8.2f}{r['fpr_near']:>7.2f}{r['f1']:>7.3f}{pairs:>9}/{len(rows) // 2}")
if a.out:
    a.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
# show which pairs the strongest LLM got wrong
for tag, preds in details.items():
    wrong = [r["id"] for r in rows if (r["label"] == "fraud") != bool(preds[r["id"]].get("is_fraud"))]
    print(tag, "model errors:", wrong)
