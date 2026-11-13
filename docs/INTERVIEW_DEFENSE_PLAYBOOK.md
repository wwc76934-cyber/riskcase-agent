# Interview defense notes

Each item states what was done, what the evidence shows, and the limit of the claim. File references are to this branch.

## 1. Header-read timeouts on the one-shot synthesis baseline (B1)

* Problem: two B1 slots in Gate 4.1 timed out before any response header. Non-streaming requests send nothing until generation ends.
* Change: `app/investigation/v3/gate4_stream.py` folds an event stream into the same response object, so reservation, settlement and unknown-execution protection are unchanged. Per-chunk read timeout 30 s, connect 10 s, total 120 s.
* Evidence: in the V4 smoke runs, stage requests that took 40 s and 72 s returned normally. No timeouts in 27 stages.
* Limit: a second failure (`gate4_input_byte_limit`) appeared in the first B1 smoke run. The default 32,000-byte input cap was too small for stage 2; it was raised to 64,000 in `app/evaluation/v4_runner.py` and that run was repeated. The failed run is kept as `..._inputlimit_failed`.

## 2. The 0/17 result

* Old rule: a stage passes only if recommended actions are inside the author's reference set and every other check holds. 15 of 17 stages carried an extra action.
* `app/evaluation/v4_score.py` adds two tiers and keeps the old result. Tier 1 hard gates: no dangerous action, citations supported, core disposition and customer state correct, mandatory evidence acquired. Tier 2: action recall and precision, stop reason, cost efficiency.
* Result on the same 17 stages: legacy 0, Tier 1 raw output 8 (`docs/G4_V4_RESCUE_EVALUATION.md`).
* Limit: nine stages failed on disposition or customer state, so the old scorer was not the main cause. Tier 1 is also weaker than the old rule, which is why both numbers are reported.
* Check against a known baseline: the deterministic workflow scores 78 of 120 stages under both rules.

## 3. Policy Guard

* Model supplies cited facts and a customer-state label. `app/investigation/v3/policy_guard.py` verifies the label against the evidence ledger, then derives disposition, stop reason and mandatory actions from a fixed table. Extra model actions become advisory notes. Unsafe actions are dropped and logged. Raw output, guarded output and each intervention are returned together.
* Result: 8 to 13 of 17 on the archived stages. On the 27 new stages: B1S 4 to 9, B2V4 4 to 9, B4N 4 to 8.
* Limit: the table was written after reading the reference vocabulary, and the same 40-case dataset is used for all numbers. Treat these as development results. A claim of generalisation needs cases written after the table was frozen.
* Expected question: "Is the guard doing the model's job?" Answer: yes for disposition, by design. The model still has to classify the customer state with correct citations, and the guard downgrades to "continue collection" when it cannot verify them. The numbers show what the model does alone and with the guard.

## 4. Planning and the cost-tier gate

* Observed earlier: a model asked to plan batched primary and fallback sources into one call.
* Change: `app/investigation/v3/tier_gate.py`, opt-in. Free reads are unrestricted. When one call asks for several paid tiers, only the cheapest runs; the rest return a zero-cost deferred receipt.
* Limit: the simulated records carry no primary or fallback relation, only a cost. The gate therefore enforces cost order, not "fallback only after primary failed". Results are in section 6.

## 5. Track A

* `docs/TRACK_A_FINAL_POSTMORTEM.md`. Qwen event vectors have mean pairwise cosine 0.69 against 0.00 for random vectors. Effective rank 92 of 1024.
* Limit: this is consistent with anisotropy hurting the downstream model; it does not isolate that as the cause. The event vocabulary is 1,918 items, not a short list. The holdout split was not re-run.

## 6. Cost-tier gate on the paid-source cases

Details: `docs/V4_GATE_STUDY.md`.

* Six paid-source cases, dynamic agent with and without the gate: 4.00 vs 3.11 evidence credits per stage, Tier 1 after guard 12 vs 13 of 18.
* The gate itself deferred a source in 1 of 18 stages. The saving most likely came from the planner rule text the model followed, not from host enforcement. Say this before the interviewer asks.
* One run per case, six cases, no interval. Report it as a direction, not a result.

## 7. Spend

* Local ledger estimate after the smoke and gate studies: 41.04 CNY of the 50 CNY project limit. Provider billing is authoritative.
* Every paid batch ran from a written grant, stopped at a batch cap, and stopped on any uncertain request.
