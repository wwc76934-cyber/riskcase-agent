"""Freeze, inspect or run the stage-four evaluation. Paid mode is explicit."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4
from app.evaluation.scenarios import build,LIVE_IDS
from app.evaluation.engine import ARMS,execute
from app.service.runtime import write_json
from app.service.store import digest


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(root):
    paths=[p for p in (root/'app').rglob('*.py')]+list((root/'investigation_skills').rglob('*.md'))
    return paths+[root/'config/siliconflow-live-v1.json',root/'config/reviewed-method-stage3.json',root/'docs/STAGE4_PROTOCOL.md',root/'requirements-investigation.txt']


def freeze(directory):
    root=Path(__file__).resolve().parents[2];directory.mkdir(parents=True,exist_ok=False)
    cases=build();write_json(directory/'scenarios.json',cases)
    # Reuse an actually reviewed prior-stage release; never synthesize it from evaluation gold.
    prior=json.loads((root/'config/reviewed-method-stage3.json').read_text(encoding='utf-8'))
    from app.investigation.knowledge import corpus,SCOPE,AS_OF
    knowledge=dict(documents=corpus(),scope=SCOPE,as_of=AS_OF,memory=prior['memory'])
    write_json(directory/'knowledge.json',knowledge)
    hashes={}
    for path in source_files(root):
        rel=path.relative_to(root);target=directory/'source'/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);hashes[rel.as_posix()]=sha(path)
    model_policy=json.loads((root/'config/siliconflow-live-v1.json').read_text(encoding='utf-8'))
    manifest=dict(version='stage4-v1',cases=60,split_counts=dict(Counter(c['split'] for c in cases)),
        scenarios_sha256=sha(directory/'scenarios.json'),knowledge_sha256=sha(directory/'knowledge.json'),source_sha256=hashes,
        live_ids=LIVE_IDS,arms=ARMS,live_arms=ARMS[1:],model_policy=model_policy,batch_id=uuid4().hex,
        max_live_runs=12,max_live_requests=72,batch_estimate_ceiling='2',project_estimate_ceiling='20',
        independence='author_generated_internal_frozen_audit_NOT_external_holdout',prior_memory_source='config/reviewed-method-stage3.json',
        knowledge_as_of='2026-10-29; historical reviewed snapshot, not a perpetual production release')
    write_json(directory/'manifest.json',manifest);return manifest


def load(directory):
    root=Path(__file__).resolve().parents[2];m=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if sha(directory/'scenarios.json')!=m['scenarios_sha256'] or sha(directory/'knowledge.json')!=m['knowledge_sha256']:raise ValueError('Frozen data changed')
    if any(sha(root/name)!=expected for name,expected in m['source_sha256'].items()):raise ValueError('Frozen source changed; do not overwrite prior run')
    return m,json.loads((directory/'scenarios.json').read_text(encoding='utf-8')),json.loads((directory/'knowledge.json').read_text(encoding='utf-8'))


def summarize(rows):
    output={}
    for arm in ARMS:
        own=[r for r in rows if r['arm']==arm]
        if not own:continue
        output[arm]=dict(n=len(own),passed=sum(r['score']['task_passed'] for r in own),
            proposed_correct=sum(r['score']['proposed_verdict_correct'] for r in own),
            mean_fact_calls=sum(r['fact_tool_attempts'] for r in own)/len(own),
            mean_host_calls=sum(r['host_tool_attempts'] for r in own)/len(own),
            real_requests=sum(r['real_requests'] for r in own),estimated_cny=str(sum(Decimal(r['estimated_cny']) for r in own)),
            mean_seconds=sum(r['elapsed_seconds'] for r in own)/len(own),
            by_split={split:dict(n=sum(r['split']==split for r in own),passed=sum(r['score']['task_passed'] and r['split']==split for r in own)) for split in sorted({r['split'] for r in own})})
    return output


def render(rows,directory):
    import html
    summary=summarize(rows);esc=lambda v:html.escape(str(v))
    body=['<!doctype html><meta charset="utf-8"><title>Agent评测结果</title><style>body{font:16px/1.6 system-ui;max-width:1120px;margin:32px auto;padding:16px;color:#193b43;background:#edf4f5}table{width:100%;border-collapse:collapse;background:white}td,th{border:1px solid #c5d8db;padding:9px}pre{white-space:pre-wrap}details{background:white;padding:16px;margin:14px 0}</style><h1>多来源证据调查评测</h1><p>自建合成内部审计；代码控制不代表模型能力，结构通过不保证正文正确。失败保留在分母。</p>']
    labels={'fixed_code':'固定全查代码','basic_agent':'基础循环','full_agent':'完整系统','no_skill':'去掉 Skill','no_knowledge':'去掉知识检索'}
    body.append('<table><tr><th>条件</th><th>客户端</th><th>结构通过</th><th>平均事实/宿主调用</th><th>真实请求</th><th>估算元</th></tr>')
    for arm,item in summary.items():
        kinds=sorted({r['client_kind'] for r in rows if r['arm']==arm})
        body.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [labels[arm],'/'.join(kinds),f'{item["passed"]}/{item["n"]}',f'{item["mean_fact_calls"]:.2f} / {item["mean_host_calls"]:.2f}',item['real_requests'],item['estimated_cny']])+'</tr>')
    body.append('</table><h2>逐案审计</h2>')
    for row in rows:
        link=row['case']+'/'+row['arm']+'/result.json'
        body.append('<details><summary>'+esc(row['case']+' · '+labels[row['arm']]+' · '+('通过' if row['score']['task_passed'] else '未通过'))+'</summary><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre><a href="'+esc(link)+'">原始结果</a></details>')
    (directory/'report.html').write_text('\n'.join(body),encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['freeze','preflight','offline','mcp','live'])
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--split',choices=['development','regression','frozen_audit','all'],default='all')
    a=p.parse_args()
    if a.action=='freeze':
        m=freeze(a.bundle);print(json.dumps({k:m[k] for k in ('version','cases','split_counts','live_ids','batch_id')},ensure_ascii=False));return
    manifest,cases,knowledge=load(a.bundle)
    if a.action=='preflight':
        print(json.dumps(dict(status='preflight_no_requests',scenarios=len(cases),live_runs=12,max_requests=72,live_ids=manifest['live_ids'],batch_estimate_ceiling='2',source_integrity='verified')));return
    if a.output is None:p.error('--output required')
    a.output.mkdir(parents=True,exist_ok=False)
    write_json(a.output/'protocol.json',manifest|dict(action=a.action,split=a.split))
    live=a.action=='live';real_mcp=a.action in ('mcp','live')
    if real_mcp:cases=[c for c in cases if c['id'] in manifest['live_ids']]
    elif a.split!='all':cases=[c for c in cases if c['split']==a.split]
    arms=manifest['live_arms'] if live else ARMS
    key=None;ledger=None
    if live:
        from app.cli.investigate_live import local_key
        from app.investigation.budget import BatchLedger
        key=local_key()
        if not key:raise ValueError('Saved credential missing')
        root=Path(__file__).resolve().parents[2];ledger=BatchLedger(root/'runs/llm-budget.sqlite',manifest['batch_id'])
    rows=[]
    for index,case in enumerate(cases):
        order=arms[index%len(arms):]+arms[:index%len(arms)]
        for arm in order:
            live_factory=None
            if live:
                from app.llm.siliconflow_client import SiliconFlowClient
                live_factory=lambda:SiliconFlowClient(key,policy=manifest['model_policy'],ledger=ledger,run_id=ledger.prefix+case['id']+':'+arm)
            record=execute(case,arm,a.output/case['id']/arm,knowledge=knowledge,live_factory=live_factory,real_mcp=real_mcp)
            rows.append(record);write_json(a.output/'results.json',rows);write_json(a.output/'summary.json',summarize(rows));render(rows,a.output)
            print(json.dumps(dict(case=record['case'],arm=arm,status=record['status'],passed=record['score']['task_passed'],real_requests=record['real_requests'])),flush=True)
            if live and (record['unknown_cost_requests'] or record['last_client_error']):
                write_json(a.output/'stopped.json',dict(reason='budget_or_client_uncertainty',attempted=len(rows),planned=12));return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
