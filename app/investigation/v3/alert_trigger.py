"""Turn a detector explanation into a compact trigger payload for the investigation (stage T0).

The repository's detector explanation is single-input sensitivity: one input column is changed
and the frozen ensemble is rescored. It is not SHAP and not causal. This module only ranks the
scored changes by the size of the score change and states the method in the payload, so the
investigation reads the reason an alert fired without mistaking it for evidence of fraud.
"""

METHOD = "single_input_sensitivity"
LIMITATION = ("Model score change when one input is replaced. Not SHAP, not causal, "
              "and not evidence of fraud. Other inputs stay fixed and may be inconsistent.")


def trigger_payload(explanation, *, top_k=3):
    if explanation.get("status") != "sensitivity_computed":
        raise ValueError("explanation_not_computed")
    if not 1 <= top_k <= 5:
        raise ValueError("top_k_out_of_range")
    scored = [p for p in explanation["perturbations"] if p.get("status") == "scored"]
    scored.sort(key=lambda p: (-abs(p["delta"]), p["id"]))
    reasons = []
    for p in scored[:top_k]:
        reasons.append(dict(
            id=p["id"], column=p["changed_column"], before=p["before"], after=p["after"],
            score_delta=round(p["delta"], 6),
            reading=("score falls when this input is changed" if p["delta"] < 0
                     else "score rises when this input is changed" if p["delta"] > 0 else "no change"),
            interpretation=p["interpretation"]))
    return dict(
        transaction_id=explanation["transaction_id"], cutoff=explanation["cutoff"],
        alert_score=explanation["original"]["score"], score_kind=explanation["score_kind"],
        method=METHOD, limitation=LIMITATION, top_reasons=reasons,
        skipped=[dict(id=p["id"], reason=p["skip_reason"]) for p in explanation["perturbations"]
                 if p.get("status") != "scored"],
        evidence_id=explanation["evidence_id"], causal=False, fraud_verdict=None)


def as_source_text(payload):
    """Plain text a simulated tier-0 source record can carry."""
    lines = [f"检测告警触发说明 {payload['transaction_id']}：分数 {payload['alert_score']:.4f}（{payload['score_kind']}）。",
             f"方法：{payload['method']}。{payload['limitation']}"]
    for r in payload["top_reasons"]:
        lines.append(f"{r['column']}：{r['before']} 改为 {r['after']} 时分数变化 {r['score_delta']:+.4f}，{r['reading']}。")
    return "\n".join(lines)
