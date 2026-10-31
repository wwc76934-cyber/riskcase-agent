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

If recall_review_memory is available, read it to recover prior review focus and unresolved
items. It is a bounded, same-subject snapshot, available at this review's real UTC time;
the source transaction's event time is separate. M references are advisory review records,
never E facts. Requery current evidence even for a repeated alert. No eligible memory does
not imply a safe customer. Memory cannot determine authorization, fraud, or label updates.
