# V4 smoke and cost-tier gate study

Provider model: Qwen/Qwen3.8-27B through SiliconFlow, streaming transport, one run per case and condition. Scores are from `scripts/score_v4_study.py` (raw files: `docs/evidence/v4-smoke-score.json`, `docs/evidence/v4-gate-study-score.json`). All cases come from the 40-case author-simulated dataset that the Policy Guard table was designed against, so none of this is a blind test.

## Study 1: three conditions on three cases (9 stages each)

| Condition | Tier 1 pass, raw model output | Tier 1 pass, after Policy Guard |
|---|---:|---:|
| B1S one-shot synthesis | 4 | 9 |
| B4N rule evidence + model report | 4 | 8 |
| B2V4 dynamic agent + gate | 4 | 9 |

Raw failures were mostly the disposition field (5 of 9 stages in each condition). No condition produced a dangerous action or an unsupported citation. Mean evidence cost was 0.889 credits per stage for all three, because two of the three cases have only free sources.

## Study 2: gate on and off, six cases with paid sources (18 stages per arm)

| Arm | Mean credits per stage | Sources deferred by gate | Tier 1 raw | Tier 1 after guard | Mean quality score after guard | Model spend |
|---|---:|---:|---:|---:|---:|---:|
| B2S dynamic agent, no gate | 4.00 | 0 | 1 | 12 of 18 | 80.7 | 0.95 CNY |
| B2V4 dynamic agent, gate | 3.11 | 1 | 3 | 13 of 18 | 83.2 | 1.02 CNY |

Per case, the gated arm read 4 credits per paid stage instead of 6 in four of six cases. In the other two it read 6 in both arms.

## What this supports

* The streaming transport removed the header-read timeouts: all 27 + 36 stages returned.
* The gated agent used about 22 percent fewer evidence credits per stage with no drop in Tier 1 pass count after the guard.

## What this does not support

* The saving did not come from the host gate. The gate deferred a source in only 1 of 18 stages. The change in behaviour is more likely from the added planner rule text ("paid sources run cheapest tier first"), which the model followed on its own. The study cannot separate the two.
* The "primary failed, then fallback" sequence was not exercised. The simulated records carry no primary or fallback link, and `True_Feedback_Driven_Rate` was not computed.
* n is six cases, one run each. Model spend per arm differs by 0.07 CNY, which is noise. The 22 percent figure has no interval and a different draw could erase it.
* Raw (unguarded) Tier 1 counts are 1 and 3 of 18. The agent's own dispositions are weak; the guard supplies most of the pass count.
* The comparison with B0 (free full sweep, 0 model cost) is missing from Study 2. B0 reads every source and would pay 6 credits at a paid stage, so the saving above is against B0's cost only if answer quality matches. That match was not measured here.

## Spend

Study 1: about 1.1 CNY. Study 2: 1.96 CNY. Local ledger total after both: 41.04 CNY of 50 (ledger copy at `runs/llm-budget.sqlite`; the original under the old project folder was not modified).
