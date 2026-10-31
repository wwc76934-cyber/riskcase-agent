---
name: fraud-alert-review
description: Review a detector-bound fraud alert using prior source transactions; retain missing authorization evidence and human judgment.
---

This is a project research workflow, not a bank's verified policy.
Read the exact alert and fraud_policy. Keep the model version, transaction identity,
event cutoff and end-of-day queue availability distinct. Scores rank alerts, not guilt.
Use fraud_history for 1h frequency and 720h positive-amount baseline, and fraud_merchant
for prior target-merchant occurrence. Check coverage and sample counts before claims.
All three scoped leads are required; supported/not_supported/insufficient_evidence are
lead statuses, not fraud labels. All-negative leads do not clear the model alert.
Request missing records when scoped evidence is incomplete; otherwise submit for analyst
review. Always retain missing cardholder authorization, device/authentication and external
case outcome. No refunds, merchant explanations, customer replies or case closure are
present in this source. Do not invent them. No funds, blocking, or model-label updates.
