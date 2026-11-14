"""Bounded local Harness load experiment; repeated source cases, no model API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import platform
import time

import numpy as np
from app.cli.fraud_bridge import prepare as prepare_bridge
from app.cli.fraud_live_acceptance import ACTOR
from app.investigation.fraud_memory import FraudMemory
from app.investigation.contracts import digest as evidence_digest
from app.investigation.fraud_service import FraudService
from app.investigation.harness import HarnessWorker,fingerprint
from app.investigation.tickets import Tickets,MockDestination
from app.service.store import digest
from app.service.runtime import write_json


def audit_evidence(evidence):
    # MCP evidence uses contracts.digest (spaced JSON). Job/journal hashes use
    # store.digest (compact JSON). Preserve both existing versioned contracts.
    return all(evidence_digest(e['result'])==e['sha256'] for e in evidence.values())


def run(release,output,*,jobs=32,workers=2):
    if type(jobs)is not int or not 4<=jobs<=64 or type(workers)is not int or not 1<=workers<=4:raise ValueError('Bounded local workload required')
    if output.exists():raise ValueError('New output required')
    output.mkdir(parents=True);baseline=prepare_bridge(release,output/'baseline')
    directory=output/'baseline/service/investigations';service=FraudService(directory);memory=FraudMemory(service.store)
    tickets=Tickets(service.store,MockDestination(directory/'controller-review.sqlite'))
    snapshots=[]
    for case in baseline['cases']:
        job=case['job_id'];row=service.store.get(job);snapshots.append(row['spec']['snapshot'])
        draft=tickets.draft(job,'Local load-test controller checks source facts; NOT independent analyst feedback.')
        tickets.approve(job,draft['hash'],reviewer=ACTOR,reviewed_claims=['findings','scope','missing'])
        item=memory.candidate(job,row['result_hash'],focus='authorization');memory.assess(item['id'],item['hash'])
        memory.publish(item['id'],item['hash'],memory.head()['head'],reviewed_claims=['source','scope','advisory_only'],reviewer=ACTOR)
    identities=[];started=time.perf_counter()
    for i in range(jobs):
        profile='full' if i%2 else 'no_knowledge'
        submitter=FraudService(directory,profile=profile)
        identity,_=submitter.submit('load-'+str(i),snapshots[(i//2)%len(snapshots)],'Bounded local repeated-source workload; requery the current three leads.')
        identities.append(identity)
    enqueued=time.perf_counter()
    write_json(output/'workload.json',dict(jobs=identities,workers=workers,code=fingerprint(),source_cases=len(snapshots),
        client='deterministic_control_NOT_LLM',memory_actor=ACTOR,burst_enqueued=True,real_model_requests=0))
    def consume():
        worker=HarnessWorker(FraudService(directory));done=[]
        while (row:=worker.step()) is not None:
            if row['id'] not in identities:raise ValueError('Unexpected queued workload')
            done.append(row['id'])
        return done
    with ThreadPoolExecutor(max_workers=workers) as pool:
        completed=[job for future in [pool.submit(consume) for _ in range(workers)] for job in future.result()]
    ended=time.perf_counter();rows=[]
    if len(completed)!=len(set(completed)) or set(completed)!=set(identities):raise ValueError('Claims incomplete or duplicated')
    for identity in identities:
        row=service.store.get(identity);state=service.store.load(identity);events=service.store.events(identity)
        claims=[e for e in events if e['kind']=='claimed']
        if len(claims)!=1:raise ValueError('Unexpected claim count in uninterrupted load run')
        claim_time=claims[0]['at']
        with service.store.transaction() as db:
            calls=db.execute('SELECT state,response,response_hash FROM calls WHERE job_id=? ORDER BY ordinal',(identity,)).fetchall()
        checked=bool(row['result']) and row['result_hash']==digest(row['result']) and state['phase']=='done'
        checked=checked and len(calls)==state['turn'] and all(c['state']=='completed' and digest(json.loads(c['response']))==c['response_hash'] for c in calls)
        checked=checked and audit_evidence(state['evidence'])
        rows.append(dict(job=identity,profile=row['spec']['profile'],state=row['state'],
            end_to_end_seconds=row['updated']-row['created'],queue_wait_seconds=claim_time-row['created'],
            service_seconds=row['updated']-claim_time,evidence=len(state.get('evidence',{})),
            response_rows=len(calls),audit_valid=checked))
    summary=dict(jobs=jobs,workers=workers,source_cases=len(snapshots),source_cases_repeated=True,
        client='deterministic_control_NOT_LLM',real_model_requests=0,states={s:sum(r['state']==s for r in rows) for s in sorted({r['state'] for r in rows})},
        enqueue_seconds=enqueued-started,drain_seconds=ended-enqueued,total_seconds=ended-started,
        jobs_per_second=jobs/(ended-enqueued),
        p50={key:float(np.median([r[key] for r in rows])) for key in ('queue_wait_seconds','service_seconds','end_to_end_seconds')},
        p95={key:float(np.percentile([r[key] for r in rows],95)) for key in ('queue_wait_seconds','service_seconds','end_to_end_seconds')},
        audit_valid_jobs=sum(r['audit_valid'] for r in rows),source_fact_checks=sum(r['evidence'] for r in rows),
        host=dict(os=platform.system(),python=platform.python_version()),
        limits=['single_local_machine','repeated_four_source_cases','no_model_or_provider_latency','no_crash_injection_in_this_run','not_production_capacity_or_analyst_time'])
    write_json(output/'per-job.json',rows);write_json(output/'summary.json',summary);print(json.dumps(summary),flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--release',type=Path,default=Path('runs/release-demo-v1'))
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--jobs',type=int,default=32);parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args();run(args.release,args.output,jobs=args.jobs,workers=args.workers)


if __name__=='__main__':main()
