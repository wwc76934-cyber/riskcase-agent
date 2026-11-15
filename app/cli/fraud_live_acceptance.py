"""Prepare offline, then explicitly authorize a bounded fraud-memory model check."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.cli.fraud_bridge import prepare as prepare_bridge
from app.investigation.fraud_memory import FraudMemory
from app.investigation.fraud_service import FraudService
from app.investigation.harness import HarnessWorker,fingerprint
from app.investigation.recording import RecordedClient
from app.investigation.tickets import Tickets,MockDestination
from app.service.store import digest
from app.service.runtime import write_json

ROOT=Path(__file__).resolve().parents[2]
ACTOR='experiment-controller-NOT-independent-human-review'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(release,output):
    if output.exists():raise ValueError('New batch directory required')
    output.mkdir(parents=True)
    baseline=prepare_bridge(release,output/'baseline')
    if len(baseline['cases'])!=4:raise ValueError('Frozen four-source-case protocol required')
    directory=output/'baseline/service/investigations';offline=FraudService(directory)
    tickets=Tickets(offline.store,MockDestination(directory/'controller-review.sqlite'));memory=FraudMemory(offline.store)
    for case in baseline['cases']:
        job=case['job_id'];row=offline.store.get(job)
        draft=tickets.draft(job,'Experiment-controller provenance check of deterministic source facts; NOT independent human review or confirmed fraud outcome.')
        tickets.approve(job,draft['hash'],reviewer=ACTOR,reviewed_claims=['findings','scope','missing'])
        item=memory.candidate(job,row['result_hash'],focus='authorization');memory.assess(item['id'],item['hash'])
        memory.publish(item['id'],item['hash'],memory.head()['head'],reviewed_claims=['source','scope','advisory_only'],reviewer=ACTOR)
    jobs=[]
    for case in baseline['cases']:
        snapshot=offline.store.get(case['job_id'])['spec']['snapshot']
        for profile in ('no_knowledge','full'):
            service=FraudService(directory,execution_mode='live',profile=profile)
            job,_=service.submit('acceptance-'+case['case_id']+'-'+profile,snapshot,'核查检测告警的三项历史行为线索；如有审阅记忆，读取作为后续核查提示，但重新查询当前事实，保留授权、认证和外部结案缺口。')
            row=service.store.get(job)
            jobs.append(dict(job=job,case_id=case['case_id'],profile=profile,spec_hash=row['spec_hash'],
                             memory_records=len(row['spec']['fraud_memory']['records'])))
    policy=json.loads((ROOT/'config/siliconflow-live-v1.json').read_text(encoding='utf-8'));policy['run_budget_cny']='0.50'
    policy['pricing_checked_on']='2026-10-31'  # Official price page still lists the frozen 8/28 upper tier.
    manifest=dict(batch_id=uuid4().hex,jobs=jobs,code=fingerprint(),runner_sha256=sha(Path(__file__)),
        protocol_sha256=sha(ROOT/'docs/FRAUD_LIVE_ACCEPTANCE_PROTOCOL.md'),policy=policy,
        max_runs=8,max_requests=48,max_estimated_cny='2',source_kind='IBM_TabFormer_synthetic',
        memory_review_actor=ACTOR,real_model_requests_at_preparation=0,independent_holdout=False)
    write_json(output/'prepared.json',manifest);write_json(output/'prepared-hash.json',dict(sha256=digest(manifest)))
    return dict(state='prepared_no_model_requests',jobs=len(jobs),batch_id=manifest['batch_id'],hash=digest(manifest),memory_review_actor=ACTOR)


def execute(output,*,live,max_estimated_cny,client_factory=None):
    if not live or Decimal(str(max_estimated_cny))!=Decimal('2'):raise ValueError('Explicit --live and the frozen 2 CNY batch ceiling required')
    manifest=json.loads((output/'prepared.json').read_text(encoding='utf-8'))
    saved=json.loads((output/'prepared-hash.json').read_text(encoding='utf-8'))
    if saved['sha256']!=digest(manifest) or manifest['code']!=fingerprint() or manifest['runner_sha256']!=sha(Path(__file__)) or manifest['protocol_sha256']!=sha(ROOT/'docs/FRAUD_LIVE_ACCEPTANCE_PROTOCOL.md'):
        raise ValueError('Frozen batch or implementation changed')
    if len(manifest['jobs'])!=8 or manifest['max_requests']!=48 or manifest['max_estimated_cny']!='2':raise ValueError('Frozen batch bounds changed')
    execution_kind='simulation_test_NOT_LLM' if client_factory is not None else 'live_provider'
    marker=output/'execution-kind.json'
    if marker.exists() and json.loads(marker.read_text())['kind']!=execution_kind:raise ValueError('Do not relabel simulated runs as provider results')
    directory=output/'baseline/service/investigations';service=FraudService(directory,execution_mode='live')
    for entry in manifest['jobs']:
        row=service.store.get(entry['job'])
        if row['spec_hash']!=entry['spec_hash'] or digest(row['spec'])!=entry['spec_hash']:raise ValueError('Prepared job changed')
        if row['state']=='running':raise ValueError('Uncertain running job requires explicit recovery; no new requests sent')
    key=None
    if client_factory is None:
        from app.cli.investigate_live import local_key
        key=local_key()
        if not key:raise ValueError('Local credential missing; no requests sent')
    if not marker.exists():write_json(marker,dict(kind=execution_kind))
    results=[]
    for entry in manifest['jobs']:
        job=entry['job'];row=service.store.get(job);record=None
        if row['state']=='queued':
            with service.store.transaction() as db:
                pending=db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if not pending or pending['id']!=job:raise ValueError('Unexpected pending job order')
            run_directory=output/'model-records'/job;run_directory.mkdir(parents=True,exist_ok=True)
            def factory():
                nonlocal record
                if client_factory is None:
                    from app.investigation.budget import BatchLedger
                    from app.llm.siliconflow_client import SiliconFlowClient
                    base=SiliconFlowClient(key,policy=manifest['policy'],ledger=BatchLedger(ROOT/'runs/llm-budget.sqlite',manifest['batch_id']),
                        run_id='multisource-'+manifest['batch_id']+':fraud:'+job)
                else:
                    base=client_factory()
                    if not getattr(base,'simulation',False):raise ValueError('Test client override must be explicitly simulated')
                record=RecordedClient(base,run_directory);return record
            row=HarnessWorker(service,client_factory=factory).step()
            if row['id']!=job:raise ValueError('Unexpected worker claim')
            if record:write_json(run_directory/'usage.json',record.snapshot())
        state=service.store.load(job) or {};report=row['result']
        result=dict(job=job,case_id=entry['case_id'],profile=entry['profile'],state=row['state'],reason=row['reason'],
            contract_passed=row['state']=='awaiting_review',answer=report['answer'] if report else state.get('proposed_answer'),
            review_memory=report.get('review_memory_snapshot') if report else None,
            evidence_count=len(state.get('evidence',{})),observed_memory_calls=sum(o.get('kind')=='review_memory' for o in state.get('observations',[])),
            model_turns=state.get('turn'),independent_human_validation=False)
        results.append(result);write_json(output/'results-so-far.json',results)
        print(json.dumps(dict(case_id=entry['case_id'],profile=entry['profile'],state=row['state'],reason=row['reason'])),flush=True)
    summary=dict(batch_id=manifest['batch_id'],execution_kind=execution_kind,results=results,
        groups={profile:dict(runs=len(rows:=[r for r in results if r['profile']==profile]),passed=sum(r['contract_passed'] for r in rows)) for profile in ('no_knowledge','full')},
        limits=['observed_development_source_cases','memory_prepared_by_experiment_controller','not_fraud_accuracy','not_human_efficiency','no_causal_gain_claim'])
    write_json(output/'summary.json',summary);return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','run'])
    parser.add_argument('--release',type=Path,default=Path('runs/release-demo-v1'));parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true');parser.add_argument('--max-estimated-cny');args=parser.parse_args()
    result=prepare(args.release,args.output) if args.action=='prepare' else execute(args.output,live=args.live,max_estimated_cny=args.max_estimated_cny)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
