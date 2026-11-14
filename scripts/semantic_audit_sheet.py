"""Write a CSV for a human check of the generated labels (random stratified sample of the test split).

    python -m scripts.semantic_audit_sheet [n_per_kind]

Columns: id, generator_label, kind, text, your_label (blank), note (blank). If your_label differs from the
generator label for more than a few rows, the benchmark labels are not trustworthy and the report must say so.
"""
import csv
import random
import sys
from pathlib import Path

from app.semantic.benchmark import read_jsonl

n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
rows = read_jsonl("data/semantic/test.ds.jsonl")
rng = random.Random(7)
chosen = []
for kind in ("fraud", "near", "ordinary"):
    pool = [r for r in rows if r["kind"] == kind]
    chosen += rng.sample(pool, min(n, len(pool)))
rng.shuffle(chosen)
out = Path("docs/evidence/semantic-audit-sheet.csv")
with out.open("w", encoding="utf-8-sig", newline="") as handle:
    w = csv.writer(handle)
    w.writerow(["id", "kind", "generator_label", "text", "your_label(fraud/legit)", "note"])
    for r in chosen:
        w.writerow([r["id"], r["kind"], r["label"], r["text"], "", ""])
print("wrote", out, len(chosen), "rows")
