# Gate 4.1 offline re-evaluation with the V4 evaluator and Policy Guard

No model request was made. Inputs are the 17 stage reports delivered in `gate4-formal-tranche-001-r2` (7 slots). Recompute with:

```
python -m scripts.re_evaluate_g4_offline <dataset> <preregistration> <live> [out.json]
```

The dataset and preregistration are regenerated from `app/evaluation/v31_query_benchmark.py`. Their hashes equal the registered values (dataset `4c859f5f...`, schedule `12991421...`, protocol `4e1ffe83...`).

## Results

| View | B1 (5) | B2 (6) | B4 (6) | All (17) |
|---|---:|---:|---:|---:|
| Legacy strict exact match | 0 | 0 | 0 | **0** |
| V4 Tier 1 `business_safe_pass`, raw model output | 2 | 4 | 2 | **8** |
| V4 Tier 1 after Policy Guard | 3 | 6 | 4 | **13** |
| Legacy strict after Policy Guard | 3 | 3 | 4 | 10 |

Tier 1 gate failures, 17 stages:

| Gate | Raw | After guard |
|---|---:|---:|
| Dangerous action | 0 | 0 |
| Citation fidelity | 2 | 2 |
| Core disposition and customer state | 9 | 2 |
| Mandatory evidence | 3 | 3 |

## How to read this

* Replacing the exact-match action rule alone lifts 0 to 8. The other nine stages have a wrong disposition or customer state, so the old scorer was not the only cause.
* The Policy Guard (`app/investigation/v3/policy_guard.py`) lifts 8 to 13. It maps the model's cited customer-state to disposition, stop reason and mandatory actions through a fixed matrix.
* **The matrix was written after reading the reference vocabulary and is tested on the same 17 stages.** The 13 is in-sample. It is not evidence of generalisation. A fresh run on unseen cases is required before claiming it.
* The guard cannot repair missing evidence (3) or unsupported citations (2). Those are model and workflow faults.
* Tier 1 is weaker than the legacy rule: counter-evidence, required gaps, stop reason and action-set exactness are demoted to Tier 2 scoring. Both numbers are reported.
* The B0 workflow on all 120 stages of the 40 cases scores 78 under both rules, so the new gates do not inflate a known baseline.
* Cost efficiency is not scored here. The archived runs carry no per-source credits.
* n is 17 stages from 7 slots, with 2 B1 stages undelivered after timeouts. No confidence interval is claimed.
