"""Freeze and evaluate raw dossiers; live execution requires a separately authorized batch."""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import shutil
from uuid import uuid4

from app.cli.agent_evaluation import sha
from app.evaluation.dossier_cases import build, LIVE_IDS
from app.evaluation.dossier import execute
from app.service.runtime import write_json

STRATEGIES = ['all_context', 'adaptive']


def freeze(directory):
    root = Path(__file__).resolve().parents[2]
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory/'cases.json', build())
    paths = list((root/'app').rglob('*.py')) + list((root/'dossier_skills').rglob('*.md'))
    paths += [root/'config/siliconflow-live-v1.json', root/'docs/STAGE5_PROTOCOL.md', root/'docs/STAGE5_REPAIR_PROTOCOL.md',root/'docs/STAGE5_V4_PROTOCOL.md', root/'requirements-investigation.txt']
    hashes = {}
    for p in paths:
        rel = p.relative_to(root);target = directory/'source'/rel
        target.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(p, target)
        hashes[rel.as_posix()] = sha(p)
    manifest = dict(version='raw-dossier-v4', batch_id=uuid4().hex, cases_sha256=sha(directory/'cases.json'),
        source_sha256=hashes, live_ids=LIVE_IDS, strategies=STRATEGIES,
        model_policy=json.loads((root/'config/siliconflow-live-v1.json').read_text(encoding='utf-8')),
        max_live_runs=12, max_live_requests=72, max_estimated_cny='2', project_max_cny='20',
        independence='observed_case_repair_regression_NOT_external_holdout',max_draft_feedback_turns=1)
    write_json(directory/'manifest.json', manifest)
    return manifest


def load(directory):
    root = Path(__file__).resolve().parents[2]
    m = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if sha(directory/'cases.json') != m['cases_sha256']:raise ValueError('Frozen cases changed')
    if any(sha(root/name) != expected for name, expected in m['source_sha256'].items()):
        raise ValueError('Source changed; preserve previous results and freeze new version')
    return m, json.loads((directory/'cases.json').read_text(encoding='utf-8'))


def summarize(rows):
    result = {}
    for strategy in STRATEGIES:
        own = [r for r in rows if r['strategy'] == strategy]
        if not own:continue
        result[strategy] = dict(n=len(own), passed=sum(r['score']['task_passed'] for r in own),
            decisions_correct=sum(r['score']['decision_correct'] for r in own),
            facts_correct=sum(r['score']['values_correct'] for r in own), facts_total=4*len(own),
            grounded_claims=sum(r['score']['grounded_claims'] for r in own),
            policy_queries=sum(r['policy_queries'] for r in own),
            mean_originals=sum(r['originals_read'] for r in own)/len(own),
            mean_seconds=sum(r['elapsed_seconds'] for r in own)/len(own),
            real_requests=sum(r['model_requests'] for r in own),
            estimated_cny=str(sum(Decimal(r['estimated_cny']) for r in own)),
            client_kinds=sorted({r['client_kind'] for r in own}))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['freeze', 'preflight', 'offline', 'mcp', 'live'])
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.action == 'freeze':
        m = freeze(a.bundle);print(json.dumps({k:m[k] for k in ('version','batch_id','live_ids')}));return
    m, cases = load(a.bundle)
    if a.action == 'preflight':
        print(json.dumps(dict(status='no_network', cases=len(cases), live_ids=m['live_ids'], max_live_runs=12, max_live_requests=72, max_estimated_cny='2')));return
    if a.output is None:p.error('--output required')
    a.output.mkdir(parents=True, exist_ok=False)
    write_json(a.output/'protocol.json', m)
    if a.action in ('mcp', 'live'):cases = [c for c in cases if c['id'] in m['live_ids']]
    key = ledger = None
    if a.action == 'live':
        from app.cli.investigate_live import local_key
        from app.investigation.budget import BatchLedger
        key = local_key()
        if not key:raise ValueError('Credential missing')
        ledger = BatchLedger(Path(__file__).resolve().parents[2]/'runs/llm-budget.sqlite', m['batch_id'])
    rows = []
    for i, case in enumerate(cases):
        for strategy in STRATEGIES[i%2:] + STRATEGIES[:i%2]:
            factory = None
            if key:
                from app.llm.siliconflow_client import SiliconFlowClient
                factory = lambda: SiliconFlowClient(key, policy=m['model_policy'], ledger=ledger, run_id=ledger.prefix+case['id']+':'+strategy)
            row = execute(case, strategy, a.output/case['id']/strategy, live_factory=factory, real_mcp=a.action in ('mcp','live'))
            rows.append(row);write_json(a.output/'results.json', rows);write_json(a.output/'summary.json', summarize(rows))
            print(json.dumps(dict(case=case['id'], strategy=strategy, state=row['state'], passed=row['score']['task_passed']), ensure_ascii=False), flush=True)
            if key and (row['unknown_cost_requests'] or row['last_client_error']):
                write_json(a.output/'stopped.json', dict(reason='uncertain_or_budget_stop', attempted=len(rows)));return 1
    return 0


if __name__ == '__main__':raise SystemExit(main())
