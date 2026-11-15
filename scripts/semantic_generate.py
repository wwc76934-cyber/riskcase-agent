"""Generate one split of the semantic fraud benchmark.

    python -m scripts.semantic_generate <split> <generator_model> [--limit N] [--workers K]

Output: data/semantic/<split>.<tag>.jsonl (resumable). Spend goes to runs/semantic-ledger.sqlite
with a hard cap (default 45 CNY, override with --cap).
"""
import argparse
from pathlib import Path

from app.semantic.benchmark import generate, plan, read_jsonl
from app.semantic.llm import LLM, Budget

TAGS = {"deepseek-ai/DeepSeek-V4-Flash": "ds", "Qwen/Qwen3.8-27B": "qw", "Qwen/Qwen2.5-7B-Instruct": "q7"}

parser = argparse.ArgumentParser()
parser.add_argument("split")
parser.add_argument("model")
parser.add_argument("--limit", type=int)
parser.add_argument("--workers", type=int, default=4)
parser.add_argument("--cap", default="45")
parser.add_argument("--ledger", default="runs/semantic-ledger.sqlite")
a = parser.parse_args()

specs = plan(a.split, generator=a.model)
if a.limit:
    # spread the limit over kinds so a smoke test sees every kind
    specs = specs[::max(1, len(specs) // a.limit)][:a.limit]
out = Path("data/semantic") / f"{a.split}.{TAGS[a.model]}.jsonl"
budget = Budget(a.ledger, a.cap)
llm = LLM(budget)
counts = generate(llm, specs, a.model, out, workers=a.workers)
print(a.split, "planned", len(specs), counts, "file lines", len(read_jsonl(out)), "spent CNY", budget.spent())
