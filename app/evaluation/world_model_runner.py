"""Prepare and execute explicit, bounded model phases; C stays controller-only."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time
from uuid import uuid4

from app.evaluation.world_runner import checked_dataset
from app.evaluation.world_score import score
from app.evaluation.world_feedback import memory_condition,publish_methods
from app.investigation.harness import HarnessWorker,fingerprint
from app.investigation.world_service import WorldService
from app.investigation.recording import RecordedClient
from app.investigation.contracts import digest
from app.risk.history import sha256_file
from app.service.runtime import write_json

ROOT=Path(__file__).resolve().parents[2]
EXTRA_CODE=['app/evaluation/world_model_runner.py','app/evaluation/world_score.py','app/evaluation/world_feedback.py',
            'app/llm/siliconflow_client.py','app/llm/world_ledger.py','app/llm/cost_ledger.py','app/evaluation/world_analysis.py','config/fraud-world-scoring-v1.json',
            'docs/AGENT_VALUE_BENCHMARK_PROTOCOL_V1.md']


def propose(dataset_directory,output):
    dataset_directory,dataset=checked_dataset(dataset_directory);output=Path(output).resolve()
    if output.exists():raise FileExistsError('New upgrade directory required')
    output.mkdir(parents=True)
    groups={
      'development':dict(case_ids=[t['case_id'] for t in dataset['tasks'] if t['fold']=='development'],conditions=['B1','B2'],max_runs=24),
      'main_eval':dict(case_ids=[t['case_id'] for t in dataset['tasks'] if t['fold'] in ('frozen','challenge')],conditions=['B1','B2','B3'],max_runs=180),
      'memory_eval':dict(case_ids=[t['case_id'] for t in dataset['tasks'] if t['fold']=='challenge'],conditions=['raw_history','stale','wrong'],max_runs=60),
    }
    policy=json.loads((ROOT/'config/siliconflow-world-v2.json').read_text());policy['project_budget_cny']='25.00'
    import sqlite3
    from contextlib import closing
    ledger_path=ROOT/'runs/llm-budget.sqlite';spent=Decimal('0')
    if ledger_path.exists():
        with closing(sqlite3.connect(ledger_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            spent=sum((Decimal(e if e is not None else r) for r,e in db.execute('SELECT reserved_cny,estimated_cny FROM reservations')),Decimal('0'))
    proposal=dict(id=uuid4().hex,state='pending_user_authorization',grant_message=None,
                  dataset=str(dataset_directory),dataset_sha256=sha256_file(dataset_directory/'dataset.json'),
                  groups=groups,max_runs=264,max_requests=1584,max_estimated_cny='8',project_limit_cny='25',
                  previous_project_limit_cny='20',previous_project_estimated_cny=str(spent),
                  policy=policy,destination='https://api.siliconflow.cn/v1/chat/completions',
                  payload_scope='IBM synthetic A projections, explicitly simulated B originals, tool results, development feedback and procedural memory; no C heldout truth, real personal data, ULB or LongMemEval text',
                  automatic_retries=0,limits='Development repairs consume the same 24-slot allowance; frozen results are not rerun for tuning')
    write_json(output/'proposal.json',proposal);write_json(output/'proposal-hash.json',dict(sha256=digest(proposal)))
    return dict(state=proposal['state'],upgrade_id=proposal['id'],max_runs=264,max_requests=1584,max_estimated_cny='8',project_limit_cny='25',model=policy['model'])


def approved(output,*,allow_pending=False):
    proposal=json.loads((output/'proposal.json').read_text());saved=json.loads((output/'proposal-hash.json').read_text())
    if digest(proposal)!=saved['sha256']:raise ValueError('Upgrade proposal changed')
    path=output/'authorization.json'
    if not path.exists():
        if not allow_pending:raise ValueError('No explicit authorization receipt; no model requests sent')
        authorization=proposal
    else:
        authorization=json.loads(path.read_text())
        expected={**proposal,'state':'approved','grant_message':authorization.get('grant_message')}
        if not authorization.get('grant_message') or authorization!=expected:raise ValueError('Approval must bind the exact proposal')
    dataset_directory,dataset=checked_dataset(proposal['dataset'])
    if sha256_file(dataset_directory/'dataset.json')!=proposal['dataset_sha256']:raise ValueError('Authorized dataset changed')
    return authorization,dataset_directory,dataset


def record_authorization(output,*,grant_message,explicitly_approved):
    if explicitly_approved is not True or not isinstance(grant_message,str) or not grant_message.strip():
        raise ValueError('The operator must supply an actual explicit approval, never infer it from silence')
    output=Path(output).resolve();proposal=json.loads((output/'proposal.json').read_text())
    if digest(proposal)!=json.loads((output/'proposal-hash.json').read_text())['sha256']:raise ValueError('Proposal changed')
    path=output/'authorization.json'
    if path.exists():raise FileExistsError('Authorization receipt already exists')
    value={**proposal,'state':'approved','grant_message':grant_message}
    write_json(path,value);return dict(state='approved',id=proposal['id'],estimated_limit_cny='8',project_limit_cny='25')


def execution_policy(output,authorization):
    """Recorded technical timeout change; cannot widen any paid/data/model scope."""
    policy=deepcopy(authorization['policy']);path=Path(output)/'execution-timeouts.json'
    if path.exists():
        amendment=json.loads(path.read_text())
        if amendment['authorization_hash']!=digest(authorization) or set(amendment['overrides'])!={'request_timeout_seconds','run_deadline_seconds'}:
            raise ValueError('Timeout amendment cannot change financial/model/data authorization')
        policy.update(amendment['overrides'])
        from app.llm.siliconflow_client import validate_policy
        validate_policy(policy)
    return policy


def budget_amendment(output,authorization):
    path=Path(output)/'budget-amendment.json'
    if not path.exists():return None
    amendment=json.loads(path.read_text())
    if amendment['base_authorization_hash']!=digest(authorization):raise ValueError('Budget amendment does not bind original scope')
    return amendment


def archive_code(directory):
    import shutil
    paths=list((ROOT/'app/investigation').glob('*.py'))+[ROOT/n for n in EXTRA_CODE+[
        'app/tools/case_tools.py','app/risk/investigation.py','app/risk/case_snapshot.py',
        'app/service/store.py','app/service/runtime.py','app/agent/react_agent.py','app/agent/structured_output.py',
        'app/llm/glm_client.py','app/risk/history.py','requirements-investigation.txt','requirements-service.txt']]
    paths+=list((ROOT/'world_skills').rglob('*.md'))
    hashes={}
    for path in sorted(set(paths)):
        relative=path.relative_to(ROOT).as_posix();destination=directory/'source'/relative
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination);hashes[relative]=sha256_file(path)
    return hashes


def runtime_environment():
    import importlib.metadata
    result={}
    for package in ('mcp','jsonschema','httpx','pydantic','anyio','numpy'):
        try:result[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:result[package]=None
    return result


def prepare_phase(output,phase,group,*,case_ids,conditions,prior_episode=None):
    output=Path(output).resolve();authorization,dataset_directory,dataset=approved(output,allow_pending=True)
    if not phase or not all(c.isalnum() or c in '-_' for c in phase):raise ValueError('Bounded phase name required')
    scope=authorization['groups'][group]
    if not set(case_ids)<=set(scope['case_ids']) or not set(conditions)<=set(scope['conditions']):raise ValueError('Outside authorized phase scope')
    directory=output/'phases'/phase
    if directory.exists():raise FileExistsError('New phase directory required')
    directory.mkdir(parents=True)
    jobs=[];service_directory=output/'service/investigations'
    for case_id in case_ids:
        task=next(t for t in dataset['tasks'] if t['case_id']==case_id)
        runtime=json.loads((dataset_directory/task['runtime']).read_text())
        for condition in conditions:
            mode={'B1':'none','B2':'none','B3':'reviewed'}.get(condition,condition)
            if mode=='raw_history' and not prior_episode:raise ValueError('Actual prior episode is required for raw-history comparison')
            override=memory_condition(mode,runtime,prior_episode) if mode in ('raw_history','stale','wrong') else None
            service=WorldService(service_directory,execution_mode='live',strategy='all_materials' if condition=='B1' else 'adaptive',
                                 memory_mode=mode,memory_snapshot=override)
            run_id='world-upgrade-'+authorization['id']+':'+':'.join([group,phase,case_id,condition])
            job,_=service.submit(run_id,runtime,task['question']);row=service.store.get(job)
            jobs.append(dict(job=job,case_id=case_id,condition=condition,run_id=run_id,spec_hash=row['spec_hash'],reference=task['evaluation']))
    frozen=dict(phase=phase,group=group,jobs=jobs,code=fingerprint(),runtime_environment=runtime_environment(),source_archive=archive_code(directory),extra_code={n:sha256_file(ROOT/n) for n in EXTRA_CODE},
                policy=execution_policy(output,authorization),proposal_hash=digest({**authorization,'state':'pending_user_authorization','grant_message':None}),dataset_hash=authorization['dataset_sha256'])
    write_json(directory/'prepared.json',frozen);write_json(directory/'prepared-hash.json',dict(sha256=digest(frozen)))
    return dict(phase=phase,jobs=len(jobs),requests_sent=0)


def execute_phase(output,phase,*,live=False):
    if not live:raise ValueError('Explicit live execution flag required')
    output=Path(output).resolve();authorization,dataset_directory,dataset=approved(output)
    directory=output/'phases'/phase;frozen=json.loads((directory/'prepared.json').read_text())
    saved=json.loads((directory/'prepared-hash.json').read_text())
    if digest(frozen)!=saved['sha256'] or frozen['code']!=fingerprint() or frozen['proposal_hash']!=digest({**authorization,'state':'pending_user_authorization','grant_message':None}) or any(sha256_file(ROOT/n)!=h for n,h in frozen['extra_code'].items()):
        raise ValueError('Prepared code/policy/authorization changed')
    if frozen['policy']!=execution_policy(output,authorization):raise ValueError('Prepared timeout policy changed')
    if frozen['runtime_environment']!=runtime_environment():raise ValueError('Prepared dependency versions changed')
    if any(sha256_file(directory/'source'/n)!=h for n,h in frozen['source_archive'].items()):raise ValueError('Archived phase source changed')
    from app.cli.investigate_live import local_key
    from app.llm.siliconflow_client import SiliconFlowClient
    from app.llm.world_ledger import WorldLedger
    key=local_key()
    if not key:raise ValueError('Local credential unavailable; no requests sent')
    ledger=WorldLedger(ROOT/'runs/llm-budget.sqlite',authorization,budget_amendment=budget_amendment(output,authorization))
    service=WorldService(output/'service/investigations',execution_mode='live');results=[];stop_reason=None
    for entry in frozen['jobs']:
        if fingerprint()!=frozen['code']:raise ValueError('Runtime code changed during phase')
        row=service.store.get(entry['job'])
        if row['spec_hash']!=entry['spec_hash']:raise ValueError('Frozen job changed')
        if row['state']=='running':raise ValueError('Uncertain in-flight job requires explicit recovery')
        start=time.monotonic();record=None
        if row['state']=='queued':
            ledger.register(entry['run_id'])
            with service.store.transaction() as db:
                pending=db.execute("SELECT id FROM jobs WHERE state='queued' AND json_extract(spec,'$.mode')='live' ORDER BY created,id LIMIT 1").fetchone()
            if not pending or pending['id']!=entry['job']:raise ValueError('Unexpected queued run order')
            record_dir=directory/'model-records'/entry['job'];record_dir.mkdir(parents=True,exist_ok=True)
            def factory():
                nonlocal record
                client=SiliconFlowClient(key,policy=frozen['policy'],ledger=ledger,run_id=entry['run_id'])
                claimed=service.store.get(entry['job'])
                remaining=max(0,min(frozen['policy']['run_deadline_seconds'],claimed['deadline']-service.store.clock()))
                client.started=client.clock()-(frozen['policy']['run_deadline_seconds']-remaining)
                record=RecordedClient(client,record_dir);return record
            row=HarnessWorker(service,client_factory=factory,lease_seconds=max(90,frozen['policy']['request_timeout_seconds']+30),
                              deadline_seconds=frozen['policy']['run_deadline_seconds']).step()
            if row['id']!=entry['job']:raise ValueError('Worker claimed a different case')
            if record:write_json(record_dir/'usage.json',record.snapshot())
        state=service.store.load(entry['job']) or {};report=row['result']
        answer=report['answer'] if report else state.get('proposed_answer')
        if answer is None:
            try:answer=json.loads(state.get('raw',{}).get('content',''))
            except (ValueError,TypeError):answer=None
        task=next(t for t in dataset['tasks'] if t['case_id']==entry['case_id'])
        runtime=json.loads((dataset_directory/task['runtime']).read_text());reference=json.loads((dataset_directory/task['evaluation']).read_text())
        assessed=score(runtime,reference,answer,host_accepted=row['state']=='awaiting_review')
        result=dict(case_id=entry['case_id'],condition=entry['condition'],job=entry['job'],state=row['state'],reason=row['reason'],
                    assessment=assessed,model_turns=state.get('turn',0),tool_attempts=state.get('attempts',0),
                    access_cost_units=sum(t.get('cost_units',0) for t in state.get('trace',[])),elapsed_seconds=round(time.monotonic()-start,3),
                    client_kind=(report or {}).get('client_kind'),method_memory=(report or {}).get('method_memory_snapshot'),report_hash=row['result_hash'])
        result_path=directory/(entry['job']+'-result.json')
        if result_path.exists():
            previous=json.loads(result_path.read_text())
            if any(previous[k]!=result[k] for k in ('case_id','condition','job','state','assessment','report_hash')):raise ValueError('Existing terminal result changed')
            result=previous  # Recovery must preserve original elapsed time and receipt.
        else:write_json(result_path,result)
        results.append(result)
        write_json(directory/'results-so-far.json',results)
        print(json.dumps(dict(case_id=entry['case_id'],condition=entry['condition'],state=row['state'],quality=assessed['quality'],reason=row['reason'])),flush=True)
        if record and record.snapshot().get('last_error_code'):
            stop_reason=record.snapshot()['last_error_code'];break
    summary=dict(phase=phase,group=frozen['group'],results=results,groups={},planned_jobs=len(frozen['jobs']),
                 terminal_jobs=len(results),stop_reason=stop_reason,complete=len(results)==len(frozen['jobs']))
    for condition in {r['condition'] for r in results}:
        values=[r for r in results if r['condition']==condition]
        summary['groups'][condition]=dict(runs=len(values),host_accepted=sum(r['assessment']['host_accepted'] for r in values),
          strict_reference_passed=sum(r['assessment']['strict_reference_passed'] for r in values),mean_quality=sum(r['assessment']['quality'] for r in values)/len(values))
    write_json(directory/'summary.json',summary)
    return summary


def publish_development_feedback(output,phase):
    output=Path(output).resolve();authorization,dataset_directory,dataset=approved(output)
    frozen=json.loads((output/'phases'/phase/'prepared.json').read_text())
    if frozen['group']!='development':raise ValueError('Methods cannot be learned from frozen/challenge outcomes')
    service=WorldService(output/'service/investigations');results=[]
    for entry in frozen['jobs']:
        row=service.store.get(entry['job'])
        if entry['condition']!='B2' or row['state']!='awaiting_review' or row['result'].get('client_kind')!='configured_external_client':continue
        task=next(t for t in dataset['tasks'] if t['case_id']==entry['case_id'])
        runtime=json.loads((dataset_directory/task['runtime']).read_text());reference=json.loads((dataset_directory/task['evaluation']).read_text())
        results.append(dict(job=entry['job'],**publish_methods(service.store,entry['job'],runtime,reference)))
    result=dict(phase=phase,feedback=results,published_count=sum(len(r['published']) for r in results),expert_review=False)
    path=output/'phases'/phase/'method-feedback.json'
    if path.exists():raise FileExistsError('Feedback receipt already exists')
    write_json(path,result);return result


def prior_episode(output):
    """Use an actual development episode, never a fabricated successful past."""
    output=Path(output).resolve();service=WorldService(output/'service/investigations')
    candidates=[]
    for path in sorted((output/'phases').glob('*/prepared.json')):
        frozen=json.loads(path.read_text())
        if frozen['group']!='development':continue
        for entry in frozen['jobs']:
            if entry['condition']!='B2':continue
            row=service.store.get(entry['job']);state=service.store.load(entry['job']) or {}
            if not state.get('turn'):continue
            body=dict(prior_research_episode_NOT_current_case=True,goal=row['spec']['question']['text'],
                      answer=(row['result'] or {}).get('answer') or state.get('proposed_answer') or state.get('raw'),
                      observations=state.get('observations',[]))
            candidates.append((bool(row['result']),len(state.get('evidence',{})),entry['job'],body))
    if candidates:return json.dumps(max(candidates,key=lambda c:c[:3])[3],ensure_ascii=False)
    raise ValueError('No actual prior B2 episode is available')
