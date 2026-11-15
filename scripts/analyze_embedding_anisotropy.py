"""Why static LLM embeddings of API names did not help on FraudNLP (Track A).

Offline, no model call. For each embedding table (rows = distinct API events) it reports:

* mean and std of pairwise cosine similarity (raw, and after removing the mean vector)
* top-1 / top-5 share of total variance from the covariance spectrum
* effective rank (exp of the entropy of the normalised variance spectrum)

A Gaussian table of the same shape is included as a reference.

    python -m scripts.analyze_embedding_anisotropy name=path.npy [name=path.npy ...] [--out result.json]
"""
import argparse
import json
from pathlib import Path

import numpy as np


def pairwise_cosine(x):
    x = np.asarray(x, dtype=np.float64)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    unit = x / np.where(norms == 0, 1, norms)
    sims = unit @ unit.T
    off = sims[~np.eye(len(x), dtype=bool)]
    return float(off.mean()), float(off.std())


def spectrum_stats(x):
    centered = np.asarray(x, dtype=np.float64)
    centered = centered - centered.mean(axis=0, keepdims=True)
    singular = np.linalg.svd(centered, compute_uv=False)
    variance = singular ** 2
    total = variance.sum()
    if total == 0:
        return dict(top1_share=None, top5_share=None, effective_rank=0.0, participation_ratio=0.0)
    p = variance / total
    p_nonzero = p[p > 0]
    return dict(top1_share=float(p[0]), top5_share=float(p[:5].sum()),
                effective_rank=float(np.exp(-(p_nonzero * np.log(p_nonzero)).sum())),
                participation_ratio=float(total ** 2 / (variance ** 2).sum()))


def analyse(name, x):
    x = np.asarray(x, dtype=np.float64)
    raw_mean, raw_std = pairwise_cosine(x)
    centered_mean, centered_std = pairwise_cosine(x - x.mean(axis=0, keepdims=True))
    return dict(name=name, rows=int(x.shape[0]), dims=int(x.shape[1]),
                mean_pairwise_cosine=raw_mean, std_pairwise_cosine=raw_std,
                mean_pairwise_cosine_after_centering=centered_mean,
                std_pairwise_cosine_after_centering=centered_std,
                mean_vector_norm_share=float(np.linalg.norm(x.mean(axis=0)) ** 2 / (x ** 2).sum(axis=1).mean()),
                **spectrum_stats(x))


def main(tables, out=None, seed=0):
    results = [analyse(name, np.load(path)) for name, path in tables]
    rows, dims = results[0]["rows"], results[0]["dims"]
    reference = np.random.default_rng(seed).standard_normal((rows, dims))
    results.append(analyse(f"gaussian_reference_{rows}x{dims}", reference))
    if out:
        Path(out).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tables", nargs="+", help="name=path.npy")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    pairs = [t.split("=", 1) for t in args.tables]
    for r in main(pairs, args.out):
        print(f"{r['name']:<34} {r['rows']}x{r['dims']:<5} cos={r['mean_pairwise_cosine']:+.3f}±{r['std_pairwise_cosine']:.3f} "
              f"centered={r['mean_pairwise_cosine_after_centering']:+.3f} top1={r['top1_share']:.3f} "
              f"top5={r['top5_share']:.3f} eff_rank={r['effective_rank']:.1f}")
