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

## 8. Held-out semantic cases

* Twelve new cases (`app/evaluation/v4_heldout.py`), written after the guard table was frozen. Hashes of the guard, scorer and cases were recorded before the first request (`docs/evidence/heldout-freeze.json`). Full tables: `docs/FINAL_BENCHMARK_REPORT.md`.
* Six semantic cases, 18 stages per condition. Customer state read correctly: rules 9, one-shot 18, rule evidence plus model 15, dynamic agent 15. Tier 1 pass with the guard: 14 to 15 of 18, against 3 to 5 raw.
* Expected question: "You wrote the cases and the rules, so isn't the rule baseline a strawman?" Answer: partly. The semantic cases were written knowing B0's keywords. The result shows the model handles paraphrase, not how common paraphrase is. The plain cases are the control: rules were correct on 18 of 18 stages there.
* Expected question: "Does the agent beat the fixed flow?" Answer: not in this data. One-shot synthesis read the state best, and the dynamic agent used no fewer evidence credits than the fixed rule collection.

## 9. Failures during the held-out runs

* Eight runs failed and were repeated (parallel processes breaking the ledger check, a 524,288-byte body limit on streamed bodies, first-byte timeouts, a 120 s stream cap). Each fix is in the report. Failed folders are kept and excluded from scoring.
* Each uncertain request holds 0.50 CNY in the ledger by design. Six holds remain. The runner stops after an uncertain request instead of retrying a paid call.
* Lesson: the one-process-at-a-time rule is a property of the ledger, not of the provider. Document it before anyone parallelises the runs.

## 10. Security and privacy modules

* `app/investigation/v3/untrusted_text.py` flags instruction-like source text; `pii.py` masks mobile numbers, ID numbers, Luhn-valid card numbers and e-mail addresses. Both have unit tests. Neither is wired into the live request path.
* The Policy Guard does not depend on either: a hijacked report that proposes freezing an account or closing the alert has the unsafe action removed and the disposition recomputed. It does not repair `human_review_required` or `disposition_executed`; the Tier 1 gate catches those.

## 11. Semantic fraud module (the main story for the "LLM understands fraud" question)

* One sentence: an LLM extracts scam signals with verbatim quotes, the host checks each quote exists in the text, and a fixed score decides hold, step-up or release. Details and every table: `docs/SEMANTIC_FRAUD_MODULE.md`.
* Headline: on 99 contrast pairs the 27B model got 94 right, TF-IDF 80, keyword rules 32 (sign test against TF-IDF p = 0.0005). On an easy generated test split TF-IDF was better than the zero-shot LLM (0.995 against 0.987). Say both.
* Expected question: "You wrote the test cases." Answer: 30 of 99 pairs were hand-written by me, 69 were written by DeepSeek, a model family I did not evaluate. On the DeepSeek-written part the margin is small (64 of 69 against 60 of 69 for TF-IDF), so the honest claim is a modest lead there and a large one on my hand-written pairs. The generated test split labels have not been checked by a person; the audit sheet is ready.
* Expected question: "Why not just fine-tune a classifier?" Answer: on in-distribution generated text it wins (0.995). It loses on pairs that differ only in meaning, and it gives no quotes. The LLM costs more per call, which is why the routing result matters: a 7B model first and the 27B model on 32% of cases matched the 27B model's F1 at 59% of the cost.
* Expected question: "Prompt injection?" Answer: an appended instruction cut the 7B model's own verdict from 0.986 to 0.683; the host policy over checked signals stayed at 0.915; the 27B model did not move. The policy does not read the model's verdict.
* Mistakes worth telling: the first spend ledger counted refused calls (HTTP 402, balance exhausted) at worst-case cost and showed 6.3 CNY that was never billed; fixed by treating a non-200 answer as unbilled. Running generation and extraction in parallel hit provider rate limits only after the balance ran out, which first looked like rate limiting.
