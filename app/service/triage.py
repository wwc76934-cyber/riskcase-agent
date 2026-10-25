"""Deterministic first-lead policy in the workbench; experimental LLM stays separate."""
from app.risk.adaptive_triage import run_code
from app.risk.investigation import render_report
from app.service.natural import publish


def process(worker, claim, spec, directory, checkpoint):
    from app.service.runtime import GuardedTools, write_json
    from app.tools.case_tools import CaseTools
    tools=GuardedTools(CaseTools(directory/'case/manifest.json',max_calls=4),checkpoint)
    result=run_code(tools)
    report=render_report(result['findings'],tools.evidence)
    report='按频次→金额→商户顺序，遇到支持线索或资料不足即停止；未执行项没有结论。\n\n'+report
    if tools.manifest['alert'].get('data_kind')=='self_authored_fixture':
        report='自建教学案例；分数为手工设置，仅验证执行流程。\n\n'+report
    (directory/'report.md').write_text(report,encoding='utf-8')
    write_json(directory/'trace.json',result)
    summary=dict(status='completed_validated',phase='ordered_triage',plan_hash=None,
        arm='adaptive_code',client_mode='DETERMINISTIC_NO_LLM',real_LLM_requests=0,
        tool_attempts=tools.attempts,client_requests=0,findings=result['findings'],
        checks_completed=len(result['findings']['findings']),knowledge_role='advisory_only',
        stopping_reason=result['findings']['findings'][-1]['assessment'])
    publish(worker.service,claim,directory,summary,'awaiting_review',checkpoint)
