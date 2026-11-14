"""Run the LLM extractor over a benchmark file and write predictions.

    python -m scripts.semantic_extract <data.jsonl> <variant> <model> <out.jsonl> [--workers K] [--cap CNY]

variant: clean | obf | inject. Resumable. Spend is recorded in runs/semantic-ledger.sqlite.
"""
import argparse
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

from app.semantic.benchmark import inject, obfuscate, read_jsonl
from app.semantic.extractor import PROMPT_VERSION, extract
from app.semantic.llm import LLM, Budget, BudgetExceeded, LLMUnavailable

parser = argparse.ArgumentParser()
parser.add_argument("data", type=Path)
parser.add_argument("variant", choices=["clean", "obf", "inject"])
parser.add_argument("model")
parser.add_argument("out", type=Path)
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--cap", default="45")
parser.add_argument("--ledger", default="runs/semantic-ledger.sqlite")
a = parser.parse_args()


def variant_text(case):
    if a.variant == "obf":
        return obfuscate(case["text"], case["id"])
    if a.variant == "inject":
        return inject(case["text"]) if case["label"] == "fraud" else case["text"]  # attack only the fraud class
    return case["text"]


cases = read_jsonl(a.data)
done = {r["id"] for r in read_jsonl(a.out)}
todo = [c for c in cases if c["id"] not in done]
budget = Budget(a.ledger, a.cap)
llm = LLM(budget)
lock, stats = threading.Lock(), dict(ok=0, parse=0, failed=0, stopped=False)
a.out.parent.mkdir(parents=True, exist_ok=True)


def work(case):
    if stats["stopped"]:
        return
    text = variant_text(case)
    try:
        result = extract(llm, a.model, text, purpose=f"extract-{a.variant}")
    except BudgetExceeded:
        stats["stopped"] = True
        return
    except LLMUnavailable:
        stats["failed"] += 1
        return
    stats["ok" if result["ok"] else "parse"] += 1
    row = dict(id=case["id"], variant=a.variant, model=a.model, prompt_version=PROMPT_VERSION, result=result)
    with lock, a.out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


with ThreadPoolExecutor(max_workers=a.workers) as pool:
    list(pool.map(work, todo))
print(a.data.name, a.variant, a.model, "todo", len(todo), stats, "spent", budget.spent())
