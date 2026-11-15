"""Matched sequential-investigation benchmark; paid arms require explicit --live."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.risk.task_evaluation import build_development_pack
from app.risk.adaptive_triage import ORDER, PROMPT, run_code, run_model, score
from app.service.runtime import write_json
from app.tools.case_tools import CaseTools
from app.tools.knowledge_tools import KnowledgeTools, FrozenKnowledge


def cases(root):
    old = json.loads((root/'config/investigation-development-v1.json').read_text(encoding='utf-8'))
    selected = [r for r in old['cases'] if r['id'] in ('normal_seen','large_new','burst','short_coverage','negative_current')]
    selected.append(dict(id='merchant_only',amount_cents=1000,merchant='new',history=[[-120,2000,'old']],
                         gold=['not_supported','not_supported','supported']))
    return old | dict(cases=selected)


def memory():
    text = 'Ordered triage frequency amount merchant：频次→金额→商户。遇 supported 或 insufficient_evidence 即停止，只有 not_supported 才进入下一项。不追加24小时背景查询。保留已经核查的负结果。经验不是交易证据。'
    return [['triage',1,'lesson',text,'hand_authored_benchmark_procedure','fixture-reviewer',hashlib.sha256(text.encode()).hexdigest(),0]]


def benchmark(root, directory, *, live=False):
    directory.mkdir(parents=True, exist_ok=False)
    code_paths=['app/risk/adaptive_triage.py','app/cli/triage_benchmark.py','app/agent/structured_output.py','app/tools/case_tools.py']
    fingerprints={}
    for name in code_paths:
        raw=(root/name).read_bytes()
        target=directory/'code-snapshot'/name
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        fingerprints[name]=hashlib.sha256(raw).hexdigest()
    spec = directory/'cases.json'; write_json(spec,cases(root))
    tasks = build_development_pack(spec, directory/'pack')
    policy = json.loads((root/'config/siliconflow-live-v1.json').read_text(encoding='utf-8'))
    policy.update(run_budget_cny='0.16')
    write_json(directory/'protocol.json', dict(prompt=PROMPT, tasks=[t['id'] for t in tasks], arms=['fixed_all','adaptive_code','react','react_memory'],
        memory=memory(), live=live, maximum_paid_groups=12, maximum_estimate_cny='1.92', policy=policy,
        independent_benchmark=False, code_sha256=fingerprints,
        output_policy='Strict JSON or a single complete JSON fence; at most one schema-only correction inside six total calls, never repair wrong facts.',
        limits='Reused hand-authored fixtures; deterministic code baseline is essential. No fraud detection or production value claim.'))
    records = []
    key = None
    if live:
        from app.cli.investigate_live import local_key
        key = local_key()
        if not key: raise ValueError('Saved credential unavailable')
    for task in tasks:
        for arm in ('fixed_all','adaptive_code','react','react_memory'):
            if not live and arm.startswith('react'): continue
            tools = CaseTools(Path(task['manifest']), max_calls=4)
            client = None
            if arm == 'react_memory': tools = KnowledgeTools(tools,FrozenKnowledge(memory()),scope='fixture',as_of='2026-10-23T00:00:00+00:00')
            if arm.startswith('react'):
                from app.llm.cost_ledger import CostLedger
                from app.llm.siliconflow_client import SiliconFlowClient
                client = SiliconFlowClient(key,policy=policy,ledger=CostLedger(root/'runs/llm-budget.sqlite'),run_id='triage-v5-'+uuid4().hex)
                try: result = run_model(client,tools)
                finally:
                    usage=client.snapshot(); client.close()
                result=client.scrub(result)
                result['usage']=usage
            else: result=run_code(tools,exhaustive=arm=='fixed_all')
            record = dict(case=task['id'], arm=arm, score=score(result,task['gold']), result=result)
            records.append(record)
            write_json(directory/'results.json',dict(data_kind='hand_authored_development_fixtures',real_llm_used=live,independent_benchmark=False,cases=records))
            print(json.dumps({k:record[k] for k in ('case','arm','score')},ensure_ascii=False),flush=True)
    return records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    benchmark(Path(__file__).resolve().parents[2],args.output,live=args.live)


if __name__=='__main__': main()
