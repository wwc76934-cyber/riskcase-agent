"""One-time FraudNLP holdout evaluation of the locked Track A models (U4).

Run with the working directory set to the Track A repository (branch codex/behavior-semantic-fraud), which
holds the frozen records, split, protocol and trained model files:

    cd <behavior-semantic-fraud>
    set PYTHONPATH=<site-with-sklearn-scipy>;.
    python <this file> <output.json>

The three evaluated arms are the ones named in the plan: task-trained embedding + numeric ('ordinary'),
frozen Qwen3-Embedding-0.6B 128-d + numeric ('qwen128'), and TF-IDF + logistic regression. The domain-pretrained
and random-128 arms are scored too, as context. No model is trained or tuned here. Each arm's development score
is replayed first and must match the saved development result, which shows the test inputs are built the same way.
"""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.getcwd())

import numpy as np
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
import torch

from app.risk.behavior_semantic.baselines import Classical, event_key, numeric
from scripts.behavior_v4 import run_study as rs

OUT = Path(sys.argv[1])
policy = rs.read("runs/track-a-v4-01/protocol.json")
for key in ("records", "split", "input_audit"):
    if rs.sha(policy[key]) != policy[key + "_sha256"]:
        raise SystemExit("frozen input changed: " + key)
bank = Path(policy["bank"])
manifest = rs.read(bank / "manifest.json")


def select(records, split_path, names):
    with np.load(split_path, allow_pickle=False) as split:
        indices = [split[n].tolist() for n in names]
    rows = [[None] * len(ids) for ids in indices]
    wanted = {index: (part, pos) for part, ids in enumerate(indices) for pos, index in enumerate(ids)}
    valid = 0
    with Path(records).open(encoding="utf8") as stream:
        for line in stream:
            row = json.loads(line)
            if not row["valid_sequence"]:
                continue
            if valid in wanted:
                part, pos = wanted[valid]
                rows[part][pos] = row
            valid += 1
    if any(r is None for part in rows for r in part):
        raise SystemExit("selected rows incomplete")
    return rows


train, dev, test = select(policy["records"], policy["split"], ("train", "development", "test"))
y, dy, ty = (np.array([r["is_fraud"] for r in part]) for part in (train, dev, test))
assert (len(train), len(dev), int(y.sum()), int(dy.sum())) == (63410, 21219, 60, 23), "split counts changed"
print("test rows", len(test), "positives", int(ty.sum()), flush=True)
sequences = [[rs.event_ids(r) for r in part] for part in (train, dev, test)]
counts = tuple(rs.count_bands(part) for part in sequences)
tables = rs.frozen_tables(bank, policy["qwen_full_sha256"], manifest)
prep = StandardScaler().fit(numeric(train))
numbers = tuple(prep.transform(numeric(part)).astype(np.float32) for part in (train, dev, test))
domain, _ = rs.domain_pretraining(sequences[0])
saved = np.load("runs/track-a-v4-01/domain_embedding.npy", allow_pickle=False)
assert np.allclose(domain, saved, atol=1e-5), "domain table differs from the saved one"
torch.set_num_threads(2)
groups = np.array([event_key(r) for r in test])
_, inverse = np.unique(groups, return_inverse=True)
clusters = int(inverse.max()) + 1


def interval(scores, repetitions=1000):
    rng = np.random.default_rng(20261104)
    values = []
    for _ in range(repetitions):
        weights = np.bincount(rng.integers(0, clusters, clusters), minlength=clusters)[inverse]
        if not np.dot(ty, weights):
            continue
        values.append(average_precision_score(ty, scores, sample_weight=weights))
    return np.percentile(values, [2.5, 97.5]).tolist()


results = {}
for arm in ("ordinary", "domain", "qwen128", "random128"):
    learned = arm in ("ordinary", "domain")
    table = domain if arm == "domain" else tables.get(arm)
    if learned:
        pools = counts
    else:
        pools = tuple((matrix @ table).reshape(-1, 4, 128) for matrix in counts)
    dev_scores, test_scores = [], []
    for seed in policy["seeds"]:
        folder = Path(f"runs/track-a-v4-01/gate4/{arm}/seed-{seed}")
        model = rs.Adapter(table, learned, numbers[0].shape[1])
        model.load_state_dict(torch.load(folder / "model.pt", map_location="cpu", weights_only=True))
        d = rs.predict(model, pools[1], numbers[1], policy["batch"])
        if not np.allclose(d, np.load(folder / "development-scores.npy", allow_pickle=False), atol=1e-5):
            raise SystemExit(f"development replay differs for {arm} seed {seed}")
        dev_scores.append(d)
        test_scores.append(rs.predict(model, pools[2], numbers[2], policy["batch"]))
    ens_dev, ens_test = np.mean(dev_scores, axis=0), np.mean(test_scores, axis=0)
    saved_dev = np.load(f"runs/track-a-v4-01/gate4/{arm}/ensemble-development.npy", allow_pickle=False)
    results[arm] = dict(development_AP_replay=float(average_precision_score(dy, ens_dev)), development_replay_matches_saved=bool(np.allclose(ens_dev, saved_dev, atol=1e-5)),
                        test_AP=float(average_precision_score(ty, ens_test)), test_AP_cluster95=interval(ens_test),
                        test_positives=int(ty.sum()), test_rows=len(ty), seeds=policy["seeds"])
    print(arm, results[arm]["development_AP_replay"], results[arm]["test_AP"], flush=True)

chosen = rs.read("runs/track-a-a2-01/tfidf_lr/seed-42/training.json")
model = Classical("tfidf_lr", 42, chosen["selected_C"], chosen["selected_balanced"])
model.fit(train, y)
dev_lr, test_lr = model.predict(dev), model.predict(test)
results["tfidf_lr"] = dict(development_AP_replay=float(average_precision_score(dy, dev_lr)), test_AP=float(average_precision_score(ty, test_lr)),
                           test_AP_cluster95=interval(test_lr), test_positives=int(ty.sum()), test_rows=len(ty), seeds=[42])
print("tfidf_lr", results["tfidf_lr"]["development_AP_replay"], results["tfidf_lr"]["test_AP"], flush=True)
OUT.write_text(json.dumps(dict(note="single evaluation of locked models on the sealed test split", results=results), indent=2) + "\n", encoding="utf-8")
