"""Score every method on the semantic fraud benchmark and print / save the tables.

    python -m scripts.semantic_report <out.json> [--llm-tag qw] [--sets test shift]

Expected files: data/semantic/{train.ds,test.ds,shift.qw}.jsonl and runs/semantic/<set>-<variant>-<tag>.jsonl
written by scripts.semantic_extract. Methods that have no prediction file are skipped.
"""
import argparse
import json
from pathlib import Path

from app.semantic.baselines import MARKERS, TfidfLR
from app.semantic.benchmark import inject, obfuscate, read_jsonl
from app.semantic.evaluate import rates, summarise
from app.semantic.policy import decide, is_intercept

parser = argparse.ArgumentParser()
parser.add_argument("out", type=Path)
parser.add_argument("--llm-tags", nargs="+", default=["qw"])
parser.add_argument("--sets", nargs="+", default=["test", "shift"])
a = parser.parse_args()

SET_FILE = {"test": "data/semantic/test.ds.jsonl", "shift": "data/semantic/shift.q7.jsonl"}
train = read_jsonl("data/semantic/train.ds.jsonl")


def vtext(case, variant):
    if variant == "obf":
        return obfuscate(case["text"], case["id"])
    if variant == "inject":
        return inject(case["text"]) if case["label"] == "fraud" else case["text"]
    return case["text"]


def rules_pred(text, threshold):
    return sum(m in text for m in MARKERS) >= threshold


# tune the rule threshold on train only
best = max(range(1, 8), key=lambda t: rates([dict(c, pred_fraud=rules_pred(c["text"], t), pred_type="none") for c in train])["f1"])
tfidf = TfidfLR().fit([c["text"] for c in train], [c["label"] for c in train], [c["fraud_type"] for c in train])

report = dict(rules_threshold=best, train_cases=len(train), results={})
for set_name in a.sets:
    cases = read_jsonl(SET_FILE[set_name])
    if not cases:
        continue
    for variant in ("clean", "obf", "inject"):
        texts = [vtext(c, variant) for c in cases]
        methods = {}
        methods["rules"] = [dict(c, pred_fraud=rules_pred(t, best), pred_type="none") for c, t in zip(cases, texts)]
        tf = tfidf.predict(texts)
        methods["tfidf_lr"] = [dict(c, pred_fraud=p["is_fraud"], pred_type=p["fraud_type"]) for c, p in zip(cases, tf)]
        for tag in a.llm_tags:
            path = Path(f"runs/semantic/{set_name}-{variant}-{tag}.jsonl")
            preds = {r["id"]: r["result"] for r in read_jsonl(path)}
            if not preds:
                continue
            by_id = {c["id"]: c for c in cases}
            model_rows, policy_rows, parse_fail, grounded_kept, dropped = [], [], 0, 0, 0
            for cid, res in preds.items():
                c = by_id[cid]
                if not res["ok"]:
                    parse_fail += 1
                    model_rows.append(dict(c, pred_fraud=False, pred_type="none"))
                    policy_rows.append(dict(c, pred_fraud=False, pred_type="none"))
                    continue
                grounded_kept += len(res["signals"])
                dropped += res["dropped_signals"]
                model_rows.append(dict(c, pred_fraud=res["is_fraud"], pred_type=res["fraud_type"]))
                d = decide([s["name"] for s in res["signals"]])
                policy_rows.append(dict(c, pred_fraud=is_intercept(d), pred_type=res["fraud_type"] if is_intercept(d) else "none"))
            methods[f"llm_{tag}_model"] = model_rows
            methods[f"llm_{tag}_policy"] = policy_rows
            methods[f"llm_{tag}_quality"] = [dict(parse_failures=parse_fail, signals_grounded=grounded_kept, signals_dropped=dropped,
                                                 cases=len(preds))]
        for name, rows in methods.items():
            if name.endswith("_quality"):
                report["results"][f"{set_name}/{variant}/{name}"] = rows[0]
            else:
                report["results"][f"{set_name}/{variant}/{name}"] = summarise(rows)

a.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fmt(v):
    return "-" if v is None else f"{v:.3f}"


print("rules threshold (tuned on train):", best)
print(f"{'set/variant/method':<34}{'n':>4}{'F1':>7}{'recall':>8}{'FPR_near':>10}{'FPR_ord':>9}{'type':>7}")
for key, r in report["results"].items():
    if "n" in r:
        print(f"{key:<34}{r['n']:>4}{fmt(r['f1']):>7}{fmt(r['fraud_recall']):>8}{fmt(r['fpr_near']):>10}{fmt(r['fpr_ordinary']):>9}{fmt(r['type_acc']):>7}")
    else:
        print(f"{key:<34}", r)
