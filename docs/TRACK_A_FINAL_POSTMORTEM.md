# Track A postmortem: why frozen LLM embeddings did not help on FraudNLP

Status: closed. No new model was trained and no model API was called for this note.

## Result being explained

Single development evaluation, 23 positives in 21,219 rows (`docs/behavior-semantic-v3/TRACK_A_V4_RESULTS.md` on branch `codex/behavior-semantic-fraud`):

| Event representation | AP |
|---|---:|
| Task-trained embedding | 0.498 |
| Domain embedding | 0.472 |
| Random frozen 128-d | 0.414 |
| Qwen3-Embedding-0.6B frozen, 128-d | 0.218 |

With 23 positives these APs are noisy. The paired interval for Qwen minus ordinary is about -0.41 to -0.14, so the direction is clear and the size is not.

## Measurement

`scripts/analyze_embedding_anisotropy.py` on the saved event tables. Raw numbers: `docs/evidence/track_a_anisotropy.json`.

| Table | Rows x dims | Mean pairwise cosine | Top-5 variance share | Effective rank |
|---|---|---:|---:|---:|
| Qwen3-0.6B, full | 1916 x 1024 | **+0.689** | 0.302 | 91.9 |
| Qwen3-0.6B, reduced | 1918 x 32 | **+0.706** | 0.456 | 21.1 |
| Random frozen | 1918 x 32 | 0.000 | 0.188 | 31.8 |
| TF-IDF + SVD | 1918 x 32 | +0.269 | 0.344 | 27.3 |
| Task-trained | 1918 x 32 | +0.007 | 0.387 | 25.5 |
| Gaussian reference | 1916 x 1024 | 0.000 | 0.014 | 784.1 |

## What this supports

* The Qwen table is strongly anisotropic. Any two distinct API events have cosine near 0.7, and the full 1024 dimensions behave like about 92 dimensions.
* After reduction to 32 dimensions the Qwen table keeps 21 effective dimensions, against 32 for random. Its top 5 directions carry 46 percent of variance, against 19 percent for random.
* Every event text starts with the same sentence, "Banking API operation. Namespace: ...". This was tested by removing it (section below). The prefix explains only part of the cone.
* The task-trained table has raw cosine near 0 and a large mean component only after centering, which is consistent with it learning to separate events for this task.

## What this does not show

* It does not prove anisotropy caused the AP gap. Same-shape random tables lose no information and train well, which is consistent with the story, but no experiment here changes only the anisotropy.
* The vocabulary is **1,918 distinct API events**, not a few dozen. An earlier review note that called it a small closed set is wrong.
* The full table has 1,916 rows and the others 1,918. The cause was not investigated.
* The holdout set was not re-run (see the end of this note).

## Decision

Keep Track A as a closed negative result. Do not train new sequence models or start SCAMBENCH. The detector is used upstream of the investigation agent as a fixed alert source.

## Strip-prefix ablation (done)

`scripts/track_a_prefix_ablation.py` embeds the same 1,916 event texts through the provider's embedding endpoint with the model name `Qwen/Qwen3-Embedding-0.6B`, three ways. The provider vectors for the prefixed text match the saved local table (mean row cosine 0.99996), so the two are the same model for this purpose. Raw numbers: `docs/evidence/track_a_prefix_ablation.json`.

| Input text | Mean pairwise cosine | Top-5 variance share | Effective rank (of 1024) |
|---|---:|---:|---:|
| Original, with the shared prefix | +0.690 | 0.289 | 95.2 |
| Namespace and operation symbols only | +0.608 | 0.253 | 119.1 |
| Endpoint path only | +0.595 | 0.252 | 120.6 |
| Gaussian reference | 0.000 | 0.014 | 784.1 |

* Removing the shared prefix lowers the mean cosine by about 0.08 to 0.10 and raises the effective rank from 95 to about 120.
* **Most of the cone stays.** Without any boilerplate, two different API symbols still have cosine near 0.60, and the effective rank is a sixth of the Gaussian reference. The earlier guess that the template prefix drives the collapse is only weakly supported: it is a minor contributor.
* This still does not show that anisotropy causes the lower AP of the frozen Qwen table. It shows the geometry is a property of the model on short API strings, not of this project's prompt template.

## Holdout test set (run once)

`scripts/track_a_holdout_once.py`, run in the Track A repository, scored the locked models on the sealed test split: 20,673 rows, **18 positives**. Nothing was trained or tuned. Before scoring the test rows, each arm's development score was replayed from the saved model files and matched the saved development result (all four neural arms, three seeds each, and the ensemble file). The test inputs are built by the same code path. Raw output: `docs/evidence/track_a_holdout.json`.

| Arm | Development AP | Test AP | Test AP, 95% cluster interval |
|---|---:|---:|---|
| Task-trained embedding + numeric ("ordinary") | 0.498 | **0.435** | 0.21 to 0.65 |
| Domain-pretrained embedding + numeric | 0.472 | 0.399 | 0.18 to 0.63 |
| Random frozen 128-d + numeric | 0.414 | 0.391 | 0.17 to 0.60 |
| Frozen Qwen3-Embedding-0.6B 128-d + numeric | 0.218 | 0.262 | 0.10 to 0.51 |
| TF-IDF + logistic regression (seed 42) | 0.269 | 0.200 | 0.06 to 0.44 |

* The order of the arms on test matches development. The frozen Qwen table is below the random table and the task-trained table on both splits.
* **The intervals overlap heavily.** With 18 positives, the interval for the Qwen table (0.10 to 0.51) contains the point estimates of the random and domain arms. The test split does not confirm the gap statistically; it confirms the direction.
* Clusters are exact-duplicate sequences, not users, so the intervals can still be optimistic.
* The frozen Qwen arm scored higher on test (0.262) than on development (0.218). With 18 positives that is within noise.

Track A is closed: the development result, the prefix ablation and one holdout evaluation are all in. What remains unknown is whether anisotropy causes the gap; no experiment here changes only the anisotropy.
