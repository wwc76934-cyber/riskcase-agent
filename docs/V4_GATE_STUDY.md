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


# Study 3 (V5): primary, corroborating and fallback sources on a cost ladder

Study 2 could not tell whether the host gate or the planner text caused the saving, and it never exercised "primary failed, then fallback". Study 3 builds that situation on purpose.

## Setup

* 12 held-out cases, 3 stages each (36 stages per strategy), one run per case and strategy. The simulated records now carry a role per source: primary (2 credits), corroborating (2 credits), fallback (6 credits). Case ids are `v5cost-*`.
* "Unresolved" is decided by the rule reader (`customer_state`): the primary record is unavailable, or it does not settle the customer's position. A paid follow-up read after a primary that did settle it is a premature read.
* Strategies:
  * S1 exhaustive: fetch every source at every stage (B1S).
  * S2 agent, no gate: the dynamic agent, same tools, no host precondition (B2S).
  * S3 agent, precondition gate: the host refuses a fallback or corroborating read until the primary has been read and left the case unresolved (B2P).
* Metrics: `True_Feedback_Driven_Rate` = share of stages where the primary left the case unresolved and the agent then read a follow-up source in a later model turn. `Premature_Fallback_Rate` = share of paid follow-up reads made when the primary had already settled the case. Blocked count = reads the gate refused.
* Code: `app/investigation/v3/precondition_gate.py`, `app/evaluation/v5_cost_study.py`, `scripts/v5_u3_report.py`. Evidence: `docs/evidence/v5-u3-report.json`.

## Result

| Strategy | Tier 1 after guard | Credits per run | Credit saving vs S1 | True feedback-driven rate | Premature follow-up reads | Reads blocked | Model CNY per run |
|---|---:|---:|---:|---:|---:|---:|---:|
| S1 exhaustive | 29 of 36 | 9.67 | 0 | 0.00 (no decisions) | 5 of 12 | 0 | 0.096 |
| S2 agent, no gate | 23 of 36 | 9.17 | 5% | 0.29 (4 of 14) | 5 of 13 | 0 | 0.183 |
| S3 agent, gated | 17 of 36 | 5.83 | 40% | 0.50 (7 of 14) | 0 of 7 | 17 | 0.199 |

Tier 1 raw (before the guard): 8, 6 and 4 of 36.

## What this supports

* The gate does what it is designed to do. Premature follow-up reads fall from 5 to 0, the feedback-driven rate rises from 0.29 to 0.50, and credits per run fall from 9.17 to 5.83 (36 percent against S2, 40 percent against S1). The 40 percent saving sits inside the 35 to 45 percent range the plan expected.
* An agent without the gate does not reliably use the primary result to decide. S2 made 5 premature follow-up reads out of 13 and its cost (9.17 credits) is close to exhaustive fetch (9.67).

## What this does not support

* **The saving costs accuracy.** S3 passes Tier 1 in 17 of 36 stages, S1 in 29. In five of the twelve cases (06, 08, 09, 11, 12) S3 read only the 2-credit primary for the whole run, kept the stage-1 pass and lost stages 2 and 3. In case 02 it read 8 credits and passed none of the three stages. Later stages need information that sits in other sources, and the gate lets the agent skip them whenever the primary looks settled at stage 1. The gate is cheap, and in this design it is also under-fetching.
* "Premature" is defined by the rule reader, not by a human. If the reader is wrong about a record, a justified read is counted as premature.
* S1 does not make decisions, so its feedback-driven rate of 0 is by construction.
* 12 cases, one run each, no confidence interval. The Tier 1 gap of 12 stages between S1 and S3 is large enough to survive that noise; the 6-stage gap between S1 and S2 is not clearly outside it.
* Records are simulated and the cost ladder is assumed, not observed from a provider.
* The B2S run carries a model cost twice B1S's per run (0.18 against 0.10 CNY), so the credits saved are partly offset by model spend in a real price setting. The ledger copies used for parallel runs each held 0.5 CNY for uncertain requests, which does not change the per-run figures above.

## Spend

About 5.7 CNY of committed model estimate across the three strategies (sum of per-run estimates: 1.15, 2.20, 2.38), 12 cases each, in the separate ledgers `runs/llm-budget-u3a/b/c.sqlite`. The three arms ran in parallel on copies of one ledger so the shared baseline check held.

## An untested idea

Make the gate stage-aware: allow corroborating reads at stages 2 and 3 even when the primary settled stage 1. This was not run. Running it after seeing these results and reporting only the improved number would be tuning on the held-out set.
