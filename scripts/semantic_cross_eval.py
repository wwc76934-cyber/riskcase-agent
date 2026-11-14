"""Run and score the five methods on the cross-context pairs (U1).

    python -m scripts.semantic_cross_eval [--run] [--cap CNY]

--run calls the models for rows not yet in runs/semantic/cross-<tag>.jsonl (resumable, budget-capped).
Scoring only uses saved predictions.
"""
import argparse
import json
from math import comb
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.semantic.baselines import MARKERS
from app.semantic.benchmark import read_jsonl
from app.semantic.cross_context import PROMPT_VERSION, extract_cross
from app.semantic.evaluate import rates
from app.semantic.llm import LLM, Budget, BudgetExceeded, LLMUnavailable
from app.semantic.policy import decide, is_intercept

MODELS = {"q7": "Qwen/Qwen2.5-7B-Instruct", "qw": "Qwen/Qwen3.8-27B"}

parser = argparse.ArgumentParser()
parser.add_argument("--run", action="store_true")
parser.add_argument("--cap", default="14")
parser.add_argument("--data", default="data/semantic/cross_context_pairs.jsonl")
parser.add_argument("--name", default="cross")
parser.add_argument("--train-on", help="jsonl of pairs to train TF-IDF on (default: 5-fold CV inside the evaluated set)")
a = parser.parse_args()
rows = read_jsonl(a.data)


def pair_key(r):
    return r["pair"]


def serialise(r):
    return r["statement"] + " || " + json.dumps(r["context_facts"], ensure_ascii=False)


def run_models():
    llm = LLM(Budget("runs/semantic-ledger.sqlite", a.cap))
    lock = threading.Lock()
    for tag, model in MODELS.items():
        out = Path(f"runs/semantic/{a.name}-{tag}.jsonl")
        done = {x["id"] for x in read_jsonl(out)}

        def work(r):
            if r["id"] in done:
                return
            try:
                res = extract_cross(llm, model, r["statement"], r["context_facts"])
            except (BudgetExceeded, LLMUnavailable):
                return
            with lock, out.open("a", encoding="utf-8") as h:
                h.write(json.dumps(dict(id=r["id"], model=model, prompt_version=PROMPT_VERSION, result=res), ensure_ascii=False) + "\n")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(work, rows))


def tfidf_cv():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    if a.train_on:
        train = read_jsonl(a.train_on)
        vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), sublinear_tf=True)
        clf = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced").fit(
            vec.fit_transform([serialise(r) for r in train]), [int(r["label"] == "fraud") for r in train])
        return {r["id"]: bool(p) for r, p in zip(rows, clf.predict(vec.transform([serialise(r) for r in rows])))}
    pairs = sorted({r["pair"] for r in rows})
    folds = {p: i % 5 for i, p in enumerate(pairs)}
    pred = {}
    for f in range(5):
        train = [r for r in rows if folds[r["pair"]] != f]
        test = [r for r in rows if folds[r["pair"]] == f]
        vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), sublinear_tf=True)
        X = vec.fit_transform([serialise(r) for r in train])
        clf = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced").fit(X, [int(r["label"] == "fraud") for r in train])
        for r, p in zip(test, clf.predict(vec.transform([serialise(r) for r in test]))):
            pred[r["id"]] = bool(p)
    return pred


def load(tag):
    return {x["id"]: x["result"] for x in read_jsonl(f"runs/semantic/{a.name}-{tag}.jsonl")}


def verdict(res):
    return bool(res["ok"]) and is_intercept(decide([s["name"] for s in res["signals"]]))


def unsure(res):
    if not res["ok"] or res["confidence"] < 0.8:
        return True
    if any(s["name"] in ("alibi_telemetry_contradiction", "temporal_scope_mismatch", "multi_turn_narrative_shift") for s in res["signals"]):
        return True
    return res["claim_contradicted"] != verdict(res)


def sign_test(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def main():
    if a.run:
        run_models()
    small, large = load("q7"), load("qw")
    if len(small) < len(rows) or len(large) < len(rows):
        print("predictions incomplete:", len(small), len(large), "of", len(rows))
        return
    cost = lambda res: float(res["meta"]["cny"])
    methods = {
        "rules": {r["id"]: sum(m in serialise(r) for m in MARKERS) >= 2 for r in rows},
        "tfidf_lr_5fold": tfidf_cv(),
        "q7_model": {r["id"]: bool(small[r["id"]].get("claim_contradicted")) for r in rows},
        "q7_dual_policy": {r["id"]: verdict(small[r["id"]]) for r in rows},
        "q27_model": {r["id"]: bool(large[r["id"]].get("claim_contradicted")) for r in rows},
        "q27_dual_policy": {r["id"]: verdict(large[r["id"]]) for r in rows},
    }
    esc = {r["id"]: unsure(small[r["id"]]) for r in rows}
    methods["cascade_7b_to_27b"] = {r["id"]: (verdict(large[r["id"]]) if esc[r["id"]] else verdict(small[r["id"]])) for r in rows}
    report = dict(pairs=len(rows) // 2, methods={}, by_category={}, grounding={}, cascade={})
    ids = [r["id"] for r in rows]
    pair_ids = {}
    for r in rows:
        pair_ids.setdefault(r["pair"], {})[r["label"]] = r["id"]
    ok = {}
    for name, pred in methods.items():
        rr = rates([dict(r, pred_fraud=pred[r["id"]], pred_type="none") for r in rows])
        right = {p: (pred[v["fraud"]] and not pred[v["legit"]]) for p, v in pair_ids.items()}
        ok[name] = right
        report["methods"][name] = dict(recall=rr["fraud_recall"], false_positive_rate=rr["fpr_near"], f1=rr["f1"],
                                       pairs_right=sum(right.values()), pairs=len(right))
        for cat in sorted({r["category"] for r in rows}):
            ps = [p for p in right if p.startswith(f"cross-{cat}-")]
            report["by_category"].setdefault(name, {})[cat] = f"{sum(right[p] for p in ps)}/{len(ps)}"
    for name in methods:
        if name == "tfidf_lr_5fold":
            continue
        b = sum(1 for p in ok[name] if ok[name][p] and not ok["tfidf_lr_5fold"][p])
        c = sum(1 for p in ok[name] if not ok[name][p] and ok["tfidf_lr_5fold"][p])
        report["methods"][name]["vs_tfidf"] = dict(method_only=b, tfidf_only=c, sign_test_p=round(sign_test(b, c), 4))
    for tag, res in (("q7", small), ("q27", large)):
        kept = sum(len(v["signals"]) for v in res.values() if v["ok"])
        dual = sum(v.get("dual_dropped", 0) for v in res.values() if v["ok"])
        quote = sum(v.get("quote_dropped", 0) for v in res.values() if v["ok"])
        report["grounding"][tag] = dict(kept=kept, dropped_by_fact_anchor=dual, dropped_by_quote_anchor=quote,
                                        parse_failures=sum(1 for v in res.values() if not v["ok"]),
                                        fact_anchor_drop_rate=round(dual / (kept + dual), 3) if kept + dual else None)
    spent = dict(small=sum(cost(v) for v in small.values()), large=sum(cost(v) for v in large.values()))
    cascade_cost = spent["small"] + sum(cost(large[i]) for i in ids if esc[i])
    report["cascade"] = dict(escalated=sum(esc.values()), of=len(ids), cost_small_only=round(spent["small"], 4),
                             cost_large_only=round(spent["large"], 4), cost_cascade=round(cascade_cost, 4))
    Path(f"docs/evidence/semantic-{a.name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{'method':<20}{'recall':>8}{'FPR':>7}{'F1':>7}{'pairs':>9}   by category / vs tfidf")
    for name, v in report["methods"].items():
        extra = report["by_category"][name]
        vs = v.get("vs_tfidf")
        print(f"{name:<20}{v['recall']:>8.2f}{v['false_positive_rate']:>7.2f}{v['f1']:>7.3f}{v['pairs_right']:>6}/{v['pairs']}   "
              + " ".join(f"{k[:6]}={x}" for k, x in extra.items()) + (f"  vs tfidf +{vs['method_only']}/-{vs['tfidf_only']} p={vs['sign_test_p']}" if vs else ""))
    print("grounding", json.dumps(report["grounding"]))
    print("cascade", json.dumps(report["cascade"]))


main()
