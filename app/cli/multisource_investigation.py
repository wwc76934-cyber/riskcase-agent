"""Run the stage-one MCP investigation demo; offline controls are the default."""
import argparse
import asyncio
import html
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from app.investigation.contracts import PROMPT, ANSWER, digest
from app.investigation.fixtures import development_cases
from app.investigation.gateway import connect, Gateway
from app.investigation.runner import run, score, ScriptedInvestigator
from app.service.runtime import write_json


def render(records, directory):
    parts = ['<!doctype html><meta charset="utf-8"><title>多来源证据调查</title>',
             '<style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:16px;background:#f4f6fa;color:#172435}article{background:white;padding:20px;margin:20px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}code{color:#285b88}</style>',
             '<h1>多来源证据调查 · 阶段一</h1><p>自建合成开发案例。脚本控制组不是模型；结论不是欺诈认定。此页面只展示本次冻结结果。</p>']
    for row in records:
        result = row['result']
        esc = lambda value: html.escape(str(value))
        arm_label={'model':'真实模型','fixed_all':'固定全查代码','adaptive_code':'证据分支代码'}[row['arm']]
        state_label={'completed':'已完成','failed':'执行失败','rejected_by_replay':'结论被校验器拒绝'}.get(result['status'],result['status'])
        score_label='通过' if row['score']['task_passed'] else '未通过'
        parts.append(f'<article><h2>{esc(row["case"])} · {esc(arm_label)}</h2><p>状态：{esc(state_label)} ｜ 结构结论与引用：{score_label} ｜ 工具尝试：{len(result["trace"])}</p>')
        answer=result.get('answer') or result.get('proposed_answer') or {}
        labels={'normal_change':'业务变化得到佐证','duplicate_record':'重复记录线索','needs_review':'需要进一步人工调查','insufficient_evidence':'资料不足'}
        prefix='未通过的模型提议：' if result['status']=='rejected_by_replay' else ''
        parts.append('<h3>'+prefix+esc(labels.get(answer.get('verdict'),'未形成通过校验的结论'))+'</h3>')
        parts.append('<p>'+esc(answer.get('summary','请查看保存的失败原文。'))+'</p>')
        if row.get('original_status') is not None:
            parts.append('<p>本页是当前校验器的离线重评；原始状态：'+esc(row['original_status'])+'，历史记录未改写。</p>')
        if result.get('replay_reason'): parts.append('<p>拒绝依据：'+esc(result['replay_reason'])+'</p>')
        if row['arm']=='model': parts.append('<p>模型摘要仍需复核；结构化结论通过校验不代表自由文本中的每句话都获得证实。</p>')
        parts.append('<p>证据引用：'+esc('、'.join(answer.get('evidence_ids',[])))+'</p>')
        if answer.get('missing'): parts.append('<p>待补材料：'+esc('；'.join(answer['missing']))+'</p>')
        parts.append('<h3>取证轨迹</h3><ol>')
        for event in result['trace']:
            parts.append(f'<li><code>{esc(event["tool"])}</code> {esc(event["arguments"])} → {esc(event.get("evidence_id",event.get("error")))}</li>')
        parts.append('</ol><details><summary>证据原文</summary><pre>'+esc(json.dumps(result['evidence'],ensure_ascii=False,indent=2))+'</pre></details>')
        if row['arm']=='model':
            parts.append('<details><summary>首次输出与格式纠正原文</summary><pre>'+esc(json.dumps({k:result.get(k) for k in ('last_raw_response','repair_raw_response')},ensure_ascii=False,indent=2))+'</pre></details>')
        parts.append('</article>')
    (directory/'report.html').write_text('\n'.join(parts),encoding='utf-8')


async def benchmark(directory, *, live=False, limit=12, start=0, batch_id=None):
    root = Path(__file__).resolve().parents[2]
    directory = directory.resolve()
    directory.mkdir(parents=True,exist_ok=False)
    tasks = development_cases()[start:start+limit]
    policy = json.loads((root/'config/siliconflow-live-v1.json').read_text(encoding='utf-8'))
    policy.update(run_budget_cny='1.00')  # Shared atomic batch ceiling is 2, project ceiling is 20.
    batch_id=batch_id or uuid4().hex
    paths = list((root/'app/investigation').glob('*.py'))+[Path(__file__),root/'requirements-investigation.txt']
    hashes = {}
    for source in paths:
        relative=source.relative_to(root)
        target=directory/'source'/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        hashes[str(relative)] = digest(source.read_text(encoding='utf-8'))
    write_json(directory/'protocol.json',dict(stage='roadmap-1', prompt=PROMPT, answer_schema=ANSWER,
        dataset_sha256=digest(tasks), independent_holdout=False, source_hashes=hashes, real_llm=live,
        policy=policy, batch_id=batch_id, maximum_cases=limit, maximum_estimate_cny='2.00',
        note='Hand-authored development tasks; versioned research-policy verifier does not prove free-text entailment.'))
    write_json(directory/'scoring-only-gold.json',[dict(id=t['id'],gold=t['gold']) for t in tasks])
    key = None
    if live:
        from app.cli.investigate_live import local_key
        key = local_key()
        if not key: raise ValueError('Local credential unavailable')
    records = []
    for task in tasks:
        for arm in (['model'] if live else ['fixed_all','adaptive_code']):
            case_dir=directory/task['id']/arm;case_dir.mkdir(parents=True)
            snapshot_path=case_dir/'snapshot.json';write_json(snapshot_path,task['snapshot'])
            client=ScriptedInvestigator(exhaustive=arm=='fixed_all')
            if live:
                from app.llm.siliconflow_client import SiliconFlowClient
                from app.investigation.budget import BatchLedger
                ledger=BatchLedger(root/'runs/llm-budget.sqlite',batch_id)
                client=SiliconFlowClient(key,policy=policy,ledger=ledger,run_id=ledger.prefix+task['id'])
            def save(value):
                if live: value=client.scrub(value)
                write_json(case_dir/'result.json',value)
            start=time.monotonic()
            result=None
            try:
                with (case_dir/'mcp-stderr.log').open('w',encoding='utf-8') as errlog:
                    async with connect(snapshot_path,task['snapshot'],errlog=errlog) as (sessions,handshakes):
                        write_json(case_dir/'mcp-handshakes.json',handshakes)
                        gateway=Gateway(sessions,task['snapshot'],audit_path=case_dir/'tool-events.jsonl')
                        result=await run(client,gateway,task['question'],save=save)
            except Exception as exc:
                # A teardown failure must not erase completed turns or partial raw output.
                if result is None and (case_dir/'result.json').exists():
                    result=json.loads((case_dir/'result.json').read_text(encoding='utf-8'))
                result=result or dict(answer=None,trace=[],evidence={})
                result.update(status='transport_failed',transport_error_type=type(exc).__name__)
            finally:
                if live:
                    usage=client.snapshot();client.close()
            result['elapsed_seconds']=round(time.monotonic()-start,3)
            if live: result['usage']=usage
            save(result)
            record=dict(case=task['id'],family=task['family'],arm=arm,score=score(result,task['gold']),result=result)
            records.append(record)
            write_json(directory/'results.json',dict(real_llm=live,records=records))
            render(records,directory)
            print(json.dumps({k:record[k] for k in ('case','arm','score')},ensure_ascii=False),flush=True)
            if live and result['status']!='completed':
                write_json(directory/'stopped.json',dict(case=task['id'],reason='Stop batch on execution/format/budget failure; inspect raw evidence before any new experiment.'))
                return records
    return records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--live',action='store_true',help='Requires batch-specific authorization; sends synthetic case evidence to SiliconFlow.')
    p.add_argument('--limit',type=int,choices=range(1,13),default=12)
    p.add_argument('--start',type=int,choices=range(12),default=0,help='Skip already attempted cases; preserve their original receipts.')
    p.add_argument('--batch-id',help='Reuse the original experiment identity and its shared 2 CNY ceiling when continuing after a fix.')
    a=p.parse_args()
    asyncio.run(benchmark(a.output,live=a.live,limit=a.limit,start=a.start,batch_id=a.batch_id))


if __name__=='__main__': main()
