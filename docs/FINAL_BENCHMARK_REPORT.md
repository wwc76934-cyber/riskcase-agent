# V4 held-out benchmark

Twelve AI-authored simulated cases written after the Policy Guard table was frozen (`docs/evidence/heldout-freeze.json`, hashes recorded before the first held-out request). Six are semantic cases (paraphrased denial, quoted denial, partial denial by time, late retraction, friendly fraud, hedged statement). Six use plain wording. Model: Qwen/Qwen3.8-27B through SiliconFlow, streaming transport, one run per case and condition, three arrival stages per run.

Raw data: `docs/evidence/heldout-score.json`, per-stage three-state audit in `docs/evidence/heldout-guard-audit.sqlite`. Reproduce the scoring: `python -m scripts.score_v4_study heldout <study_dir> out.json` and `python -m scripts.heldout_report <study_dir>`.

## What was run

| Condition | Meaning | Semantic cases (6) | Plain cases (6) |
|---|---|---|---|
| B0 | Deterministic rules and regex, no model | 6 | 6 |
| B1S | One-shot synthesis, streaming | 6 | not run |
| B4N | Rule evidence collection, model report | 6 | 3 (h07 to h09) |
| B2V4 | Dynamic agent with cost-tier gate | 6 | not run |

The plain-case runs for B1S and B2V4 and three for B4N are missing. The local ledger reached 46.77 of 50 CNY, and the ledger's per-run cap no longer fit in the remaining headroom.

## Results on the six semantic cases (18 stages per condition)

| Condition | Customer state correct | Tier 1 pass, raw model output | Tier 1 pass after Policy Guard | Legacy strict pass after guard | Mean evidence credits per run |
|---|---:|---:|---:|---:|---:|
| B0 rules | 9 / 18 | 6 | (not applicable) | 6 | 4.00 |
| B1S one-shot | 18 / 18 | 3 | 14 | 12 | 4.67 |
| B4N rule evidence + model | 15 / 18 | 3 | 15 | 12 | 4.00 |
| B2V4 dynamic + gate | 15 / 18 | 5 | 15 | 10 | 4.33 |

Plain cases: B0 correct on 18 of 18 stages. B4N correct on 9 of 9 and Tier 1 pass 9 of 9 after the guard (1 of 9 raw).

## What the numbers say

* On paraphrased and indirect statements, the rules read the customer state correctly in half of the stages. The model conditions read it correctly in 15 to 18 of 18. This is the clearest evidence of language understanding in the project.
* The raw model disposition is poor (3 to 5 of 18 pass Tier 1). The gain from 3 to 14 or 15 comes from the Policy Guard computing disposition and actions from the model's cited state. The model's work is the state and its citations.
* One-shot synthesis (B1S) read the state best. The dynamic agent did not beat the fixed rule collection plus model report (B4N) on accuracy or on credits.
* The stages the guarded B4N and B2V4 still miss are in h04 (late retraction) and h05 (friendly fraud). In those stages the mandatory evidence gate also fails, which points to evidence not collected rather than a misread statement.
* B1S failed the mandatory-evidence gate in four stages (h03 and h06) because it did not read the paid corroborating source, yet it still stated the customer state correctly from the free statements.

## Limits

* The semantic cases were written by the same AI author who knew the keyword rules in B0. B0's weakness on them is partly by construction. The comparison shows the model handles paraphrase; it does not estimate how often real customers paraphrase.
* No independent person reviewed the labels. All cases are simulated.
* Six cases per group, one run each. No confidence intervals. Differences of one or two stages between model conditions are noise.
* The Policy Guard was frozen before these runs, but its design was informed by the 40-case set that shares the generator and language with these cases.
* The guard leaves `human_review_required` and `disposition_executed` as the model wrote them. A hijacked report with those fields wrong still fails the Tier 1 safety gate, but the guard does not repair them.
* The cost-tier gate deferred sources rarely (see `docs/V4_GATE_STUDY.md`). The agent's credit use is not lower than the fixed rule collection.

## Run failures and spend

* Eight held-out runs failed and were repeated: two because processes ran in parallel (the ledger's baseline check breaks when another process settles a reservation, no spend), two from a 524,288-byte body limit that a streamed response exceeded, three from read timeouts before the first byte (30 s, 30 s, 60 s), and one from a 120 s total-stream cap. The failed run folders are kept with a `_failed` suffix and excluded from scoring.
* Fixes made along the way: first-byte read timeout 30 s then 60 s then 120 s, total stream deadline 120 s then 300 s, response body limit 4 MiB for streams, per-stage client deadline 480 s, input cap 48,000 bytes.
* Each uncertain request keeps a 0.50 CNY hold in the ledger. Six such holds (3.00 CNY) remain. Real provider cost for those requests is likely far below that. Provider billing is authoritative.
* Local ledger after all studies: 46.77 CNY of the 50 CNY project limit. Run spend is about 0.10 CNY per one-shot or rule run and 0.17 CNY per dynamic run.
