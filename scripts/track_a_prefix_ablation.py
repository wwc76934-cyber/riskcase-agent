"""Strip-prefix ablation for the Qwen3-Embedding-0.6B event vectors (Track A, U4).

Embeds the 1,916 FraudNLP event texts three ways through the provider's embedding endpoint (the same
model name as the frozen local table) and compares anisotropy statistics:

* prefixed:  the original text, "Banking API operation. Namespace: X. Operation: Y. Original endpoint: Z."
* ops:       "X Y" only (namespace and operation symbols)
* path:      "Z" only (the endpoint path)

It also checks that the provider's vectors for the prefixed text match the saved local table.

    python -m scripts.track_a_prefix_ablation
"""
import json
from pathlib import Path
import re

import httpx
import numpy as np

from app.llm.credentials import load_key
from scripts.analyze_embedding_anisotropy import analyse

PATTERN = re.compile(r"Namespace: (.*?)\. Operation: (.*?)\. Original endpoint: (.*?)\.$")
MODEL = "Qwen/Qwen3-Embedding-0.6B"
CACHE = Path("runs/track-a")


def embed(texts, name, key):
    path = CACHE / f"api-{name}.npy"
    if path.exists():
        return np.load(path)
    out = []
    with httpx.Client(timeout=120) as http:
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
            r = http.post("https://api.siliconflow.cn/v1/embeddings", headers={"Authorization": "Bearer " + key},
                          json={"model": MODEL, "input": batch, "encoding_format": "float"})
            r.raise_for_status()
            out += [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
    arr = np.asarray(out, dtype=np.float32)
    np.save(path, arr)
    return arr


def main():
    texts = json.load(open(CACHE / "event-manifest.json", encoding="utf-8"))["texts"]
    parts = [PATTERN.search(t) for t in texts]
    assert all(parts), "unexpected event text shape"
    variants = {
        "prefixed": texts,
        "ops_only": [f"{m.group(1)} {m.group(2)}" for m in parts],
        "path_only": [m.group(3) for m in parts],
    }
    key = load_key()
    tables = {name: embed(v, name, key) for name, v in variants.items()}
    saved = np.load(CACHE / "qwen-full.npy")
    api = tables["prefixed"]
    if saved.shape == api.shape:
        unit = lambda x: x / np.linalg.norm(x, axis=1, keepdims=True)
        match = float((unit(saved) * unit(api)).sum(axis=1).mean())
    else:
        match = None
    results = [analyse(name, tables[name]) for name in ("prefixed", "ops_only", "path_only")]
    results.append(analyse("saved_local_table", saved))
    report = dict(model=MODEL, rows=len(texts), api_vs_saved_mean_row_cosine=match, tables=results,
                  example_texts={k: v[0] for k, v in variants.items()})
    Path("docs/evidence/track_a_prefix_ablation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("api vs saved local table, mean row cosine:", match)
    for r in results:
        print(f"{r['name']:<20}{r['rows']}x{r['dims']} cos={r['mean_pairwise_cosine']:+.3f}±{r['std_pairwise_cosine']:.3f} "
              f"top5={r['top5_share']:.3f} eff_rank={r['effective_rank']:.1f}")


if __name__ == "__main__":
    main()
