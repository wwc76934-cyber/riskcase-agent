"""Pool the three contrast sets and compare methods pair by pair (exact sign test, Wilson intervals).

    python -m scripts.semantic_pooled [out.json]
"""
from math import comb, sqrt
import json
import sys
from pathlib import Path

from app.semantic.baselines import MARKERS, TfidfLR
from app.semantic.benchmark import read_jsonl
from app.semantic.policy import decide, is_intercept

SETS = [("contrast", "data/semantic/contrast.human.jsonl"), ("hard", "data/semantic/contrast_hard.human.jsonl"),
        ("cgen", "data/semantic/contrast_gen.ds.jsonl")]
train = read_jsonl("data/semantic/train.ds.jsonl")
tf = TfidfLR().fit([c["text"] for c in train], [c["label"] for c in train], [c["fraud_type"] for c in train])
pair_ok = {}  # method -> {pair key: bool}


def add(method, key, ok):
    pair_ok.setdefault(method, {})[key] = ok


for prefix, path in SETS:
    rows = read_jsonl(path)
    tfp = [p["is_fraud"] for p in tf.predict([r["text"] for r in rows])]
    rule = [sum(m in r["text"] for m in MARKERS) >= 2 for r in rows]
    llm = {tag: {x["id"]: x["result"] for x in read_jsonl(f"runs/semantic/{prefix}-clean-{tag}.jsonl")} for tag in ("qw", "q7")}
    for i in range(0, len(rows), 2):
        key = f"{prefix}/{rows[i]['pair']}"
        assert rows[i]["label"] == "fraud" and rows[i + 1]["label"] == "legit"
        add("rules", key, rule[i] and not rule[i + 1])
        add("tfidf_lr", key, tfp[i] and not tfp[i + 1])
        for tag, preds in llm.items():
            f, l = preds[rows[i]["id"]], preds[rows[i + 1]["id"]]
            add(f"llm_{tag}_model", key, bool(f.get("is_fraud")) and not l.get("is_fraud"))
            fp = is_intercept(decide([s["name"] for s in f.get("signals", [])]))
            lp = is_intercept(decide([s["name"] for s in l.get("signals", [])]))
            add(f"llm_{tag}_policy", key, fp and not lp)


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 3), round(c + h, 3)


def sign_test(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


n = len(pair_ok["rules"])
report = dict(pairs=n, methods={}, vs_tfidf={})
for m, d in pair_ok.items():
    k = sum(d.values())
    report["methods"][m] = dict(pairs_right=k, accuracy=round(k / n, 3), wilson95=wilson(k, n))
for m in pair_ok:
    if m in ("tfidf_lr",):
        continue
    b = sum(1 for key in pair_ok[m] if pair_ok[m][key] and not pair_ok["tfidf_lr"][key])
    c = sum(1 for key in pair_ok[m] if not pair_ok[m][key] and pair_ok["tfidf_lr"][key])
    report["vs_tfidf"][m] = dict(method_only=b, tfidf_only=c, sign_test_p=round(sign_test(b, c), 4))
print("pooled pairs:", n)
for m, v in report["methods"].items():
    print(f"{m:<18}{v['pairs_right']:>3}/{n}  acc {v['accuracy']:.3f}  95% {v['wilson95']}")
for m, v in report["vs_tfidf"].items():
    print(f"{m:<18} vs tfidf: method-only {v['method_only']}, tfidf-only {v['tfidf_only']}, p={v['sign_test_p']}")
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2), encoding="utf-8")
