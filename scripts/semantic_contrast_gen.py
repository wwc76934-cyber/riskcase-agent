"""Have a model from another family write minimal-pair contrast cases (scam text and legitimate text that
differ in the one decisive element). Neither the project author nor the evaluated model wrote them.

    python -m scripts.semantic_contrast_gen <generator_model> [--cap CNY]

Output: data/semantic/contrast_gen.<tag>.jsonl, two rows per pair, ids contrast_gen-<type>-<seed>-fraud/legit.
"""
import argparse
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

from app.semantic.benchmark import SYSTEM, read_jsonl, render, valid_turns
from app.semantic.llm import LLM, Budget, BudgetExceeded, LLMUnavailable, parse_json
from app.semantic.taxonomy import FRAUD_SEEDS, FRAUD_TYPES, NEAR_TYPES

parser = argparse.ArgumentParser()
parser.add_argument("model")
parser.add_argument("--cap", default="13.6")
parser.add_argument("--seeds", nargs="+", type=int, default=[6, 7, 8, 9])
a = parser.parse_args()
tag = {"deepseek-ai/DeepSeek-V4-Flash": "ds"}.get(a.model, "x")
out = Path(f"data/semantic/contrast_gen.{tag}.jsonl")
done = {r["id"] for r in read_jsonl(out)}
llm = LLM(Budget("runs/semantic-ledger.sqlite", a.cap))
lock = threading.Lock()


def prompt(ftype, seed):
    topic = FRAUD_SEEDS[ftype][seed]
    user = (f"请写一对对照对话，用于检测系统的评测。\n欺诈类型：{FRAUD_TYPES[ftype]}。场景：{topic}。\n"
            f"A 版是欺诈对话。B 版是合法对话（{NEAR_TYPES[ftype]}）。\n"
            "两个版本要尽量使用相同的开场、相同的话题和相近的措辞，只改变决定性质的那一处关键差别"
            "（例如收款方是谁、通过什么渠道、是否索要验证码或预付费用、是否要求保密）。两个版本都要像真实业务对话，各约 6 到 8 句，"
            "B 版里不要出现“这是合法的”之类直接点明性质的话。\n"
            "输出 JSON：{\"fraud\":{\"turns\":[{\"speaker\":\"客户或对方\",\"text\":\"...\"}]},"
            "\"legit\":{\"turns\":[...]},\"edit\":\"一句话说明关键差别\"}。speaker 只能是“客户”或“对方”。")
    return SYSTEM, user


def work(job):
    ftype, seed = job
    pid = f"contrast_gen-{ftype}-{seed}"
    if f"{pid}-fraud" in done:
        return
    try:
        text, _ = llm.chat(a.model, *prompt(ftype, seed), purpose="contrast-gen", max_tokens=2200, temperature=0.8)
    except (BudgetExceeded, LLMUnavailable):
        return
    obj = parse_json(text) or {}
    scam, legit = valid_turns(obj.get("fraud") or {}), valid_turns(obj.get("legit") or {})
    if not scam or not legit:
        return
    with lock, out.open("a", encoding="utf-8") as handle:
        for label, turns in (("fraud", scam), ("legit", legit)):
            handle.write(json.dumps(dict(
                id=f"{pid}-{label}", split="contrast_gen", kind="fraud" if label == "fraud" else "near", key=ftype, label=label,
                fraud_type=ftype if label == "fraud" else None, pair=pid, edit=str(obj.get("edit", ""))[:200],
                text=render(turns), truth_signals=[], generator=a.model), ensure_ascii=False) + "\n")


with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(work, [(t, s) for t in FRAUD_TYPES for s in a.seeds]))
print("pairs written", len(read_jsonl(out)) // 2)
