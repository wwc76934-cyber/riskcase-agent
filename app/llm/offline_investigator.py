"""Explicitly scripted demonstration policy, NOT a language model or learned agent."""

import json

from app.llm.fake_client import make_tool_call_message


class OfflineInvestigationClient:
    def chat(self, messages, tools=None):
        observed = [json.loads(message["content"])["data"] for message in messages
                    if message["role"] == "tool" and json.loads(message["content"]).get("ok")]
        target = next((row for row in observed if row["tool"] == "get_alert_transaction"), None)
        summaries = {row["lookback_hours"]: row for row in observed if row["tool"] == "summarize_history"}
        merchant = next((row for row in observed if row["tool"] == "query_target_merchant"), None)
        def call(name, arguments):
            return make_tool_call_message(f"offline-{len(messages)}", name, arguments)
        if target is None:
            return call("get_alert_transaction", {})
        if 1 not in summaries:
            return call("summarize_history", {"lookback_hours": 1})
        hour = summaries[1]
        frequency = "supported" if hour["prior_count"] >= 4 else "not_supported" if hour["complete_within_source"] else "insufficient_evidence"
        findings = [{"check": "high_prior_frequency_1h", "assessment": frequency, "evidence_ids": [hour["evidence_id"]]}]
        if frequency == "supported":
            if 24 not in summaries:
                return call("summarize_history", {"lookback_hours": 24})
            findings[0]["evidence_ids"].append(summaries[24]["evidence_id"])
        elif frequency != "insufficient_evidence":
            if 720 not in summaries:
                return call("summarize_history", {"lookback_hours": 720})
            if merchant is None:
                return call("query_target_merchant", {"lookback_hours": 720})
            month = summaries[720]
            amount = "insufficient_evidence"
            if month["complete_within_source"] and month["target_amount_cents"] > 0 and month["positive_amount_max_cents"] is not None:
                amount = "supported" if month["target_amount_cents"] > month["positive_amount_max_cents"] else "not_supported"
            unseen = "not_supported" if merchant["merchant_prior_count"] else "supported" if merchant["complete_within_source"] and merchant["prior_count"] else "insufficient_evidence"
            findings.extend([{"check": "amount_above_prior_positive_max_30d", "assessment": amount, "evidence_ids": [month["evidence_id"]]},
                {"check": "merchant_id_unseen_30d", "assessment": unseen, "evidence_ids": [merchant["evidence_id"]]}])
        return {"role": "assistant", "content": json.dumps({"findings": findings})}
