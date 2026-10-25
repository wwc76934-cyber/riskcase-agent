"""Deterministic four-query comparator, with the same permissions and validators."""

import json

from app.risk.investigation import CHECKS, expected_assessment, render_report, validate_findings


def run_fixed_investigation(tools):
    """No LLM and no gold answers: always query the same four observations."""
    result = {"status":"tool_error","client_mode":"FIXED_FOUR_QUERIES_NO_LLM","client_requests":0,
        "findings":None,"report_markdown":None,"error":None,"messages":[],
        "format_repair":{"attempted":False,"status":"not_applicable"}}
    for name,args in (("get_alert_transaction",{}),("summarize_history",{"lookback_hours":1}),
        ("summarize_history",{"lookback_hours":720}),("query_target_merchant",{"lookback_hours":720})):
        observation = tools.dispatch(name,json.dumps(args))
        if not observation.get("ok"):
            result["error"] = "Fixed workflow tool failed; no report published"
            break
    else:
        findings = []
        for check in sorted(CHECKS):
            matches = [(ref,expected_assessment(check,item)) for ref,item in tools.evidence.items()
                if expected_assessment(check,item) is not None]
            ref,status = matches[0]
            findings.append({"check":check,"assessment":status,"evidence_ids":[ref]})
        body = validate_findings(json.dumps({"findings":findings}),tools.evidence)
        result.update(status="completed_validated",findings=body,report_markdown=render_report(body,tools.evidence))
    result.update(tool_attempts=tools.attempts,evidence=tools.evidence,tool_trace=tools.trace)
    return result
