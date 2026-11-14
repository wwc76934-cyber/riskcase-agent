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
* A likely cause is visible in `runs/track-a-public-event-bank-01/manifest.json`: every event text starts with the same sentence, "Banking API operation. Namespace: ...". Shared boilerplate pushes all vectors the same way. This was not tested by removing the prefix.
* The task-trained table has raw cosine near 0 and a large mean component only after centering, which is consistent with it learning to separate events for this task.

## What this does not show

* It does not prove anisotropy caused the AP gap. Same-shape random tables lose no information and train well, which is consistent with the story, but no experiment here changes only the anisotropy.
* The vocabulary is **1,918 distinct API events**, not a few dozen. An earlier review note that called it a small closed set is wrong.
* The full table has 1,916 rows and the others 1,918. The cause was not investigated.
* The holdout set was not re-run. The numbers above are the existing development result.

## Decision

Keep Track A as a closed negative result. Do not train new sequence models or start SCAMBENCH. The detector is used upstream of the investigation agent as a fixed alert source.
