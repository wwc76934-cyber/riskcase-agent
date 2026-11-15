"""New development delivery pilot; live execution requires a separate bound grant."""
from copy import deepcopy
from decimal import Decimal
import hashlib,json
import sqlite3,time,importlib.metadata,shutil
from pathlib import Path
from app.investigation.contracts import digest
from app.service.store import digest as stored_result_digest
from app.investigation.harness_store import HarnessStore
from app.investigation.v3.world import InvestigationWorld
from app.investigation.v3.output import AcquiredEvidence,extraction_schema
from app.investigation.v3.task_output import TaskReportContract
from app.investigation.v3.delivery import StagedDelivery,request_for

SELECTION=[('quoted_speech',0),('negation_scope',0),('payment_scope',0),('later_amendment',0),('later_amendment',4),('limited_export',0)]
ROOT=Path(__file__).resolve().parents[2]


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def ledger_for(packet):
    return AcquiredEvidence(case_id=packet['case_id'],cutoff=packet['cutoff'],as_of=packet['as_of'],
        subject_id=packet['subject_id'],transaction_ids=packet['transaction_ids'],target_id=packet['target_id']).ingest(packet['evidence'])


def byte_bound(ledger,contract):
    """Conservative oversized report context, not an oracle/model answer."""
    schema=contract.extraction_schema(ledger);fact_schema=schema['properties']['facts']
    count=fact_schema['maxItems'];ref_count=fact_schema['items']['properties']['refs']['maxItems']
    max_value=fact_schema['items']['properties']['value']['maxLength']
    lines=sorted(ledger.lines.items(),key=lambda item:len(json.dumps(item[1],ensure_ascii=False).encode()),reverse=True)[:ref_count]
    facts={f'F{i}':dict(id=f'F{i}',predicate='customer_authorization',value='\U0001f9ea'*max_value,
        transaction_ids=sorted(ledger.transaction_ids),citations=[dict(handle=k,**deepcopy(v)) for k,v in lines]) for i in range(1,count+1)}
    oversized=dict(contract='v3-extraction-v2',case_id=ledger.case_id,evidence_binding=digest(ledger.model_context()),facts=facts,hash=digest(facts),semantic_truth_validated=False)
    requests=[request_for('extract',schema,ledger.model_context()),
              request_for('report',contract.schema,dict(extraction=oversized,read_event_handles=ledger.events))]
    # Includes a separate margin for the model/temperature/response-format envelope.
    return max(len(json.dumps(r,ensure_ascii=False).encode())+1024 for r in requests)


def prepare(dataset,output):
    dataset,output=Path(dataset),Path(output)
    if output.exists():raise FileExistsError('New development pilot proposal required')
    manifest=load(dataset/'dataset.json')
    if sha(dataset/'dataset.json')!=load(dataset/'dataset-sha256.json')['sha256']:raise ValueError('Candidate dataset changed')
    selected=[]
    for family,index in SELECTION:
        entry=next(e for e in manifest['entries'] if e['fold']=='development' and e['family']==family and e['template_id']==family+'-'+str(index))
        path=dataset/entry['runtime']
        if path.resolve().parent!=(dataset/'runtime').resolve() or sha(path)!=entry['runtime_sha256']:raise ValueError('Source packet changed')
        runtime=load(path)
        if any(r['cost_units'] for r in runtime['records']):raise ValueError('This pilot isolates delivery; costly planning is a later comparison')
        selected.append((entry,runtime))
    output.mkdir(parents=True);(output/'source-packets').mkdir();jobs=[];bounds=[]
    for entry,runtime in selected:
        for repeat in (1,2):
            world=InvestigationWorld(runtime)
            for stage in range(3):
                if stage:world.advance(stage)
                bodies=[world.overview(),world.read(sorted(world.eligible()))]
                evidence={f'E{i}':dict(result=body,sha256=digest(body)) for i,body in enumerate(bodies,1)}
                packet=dict(case_id=entry['case_id'],repeat=repeat,stage=stage,cutoff=runtime['cutoff'],as_of=runtime['stage_times'][stage],
                    subject_id=runtime['subject_id'],target_id=runtime['alert']['transaction_id'],
                    transaction_ids=[r['transaction_id'] for r in runtime['transactions']],evidence=evidence)
                name=f"{entry['case_id']}-r{repeat}-T{stage}";path=output/'source-packets'/(name+'.json');write_new(path,packet)
                ledger=ledger_for(packet);bound=byte_bound(ledger,TaskReportContract(packet['target_id']))
                if bound>60000:raise ValueError('Bounded report context exceeds proposed request size; no model call sent')
                bounds.append(bound);jobs.append(dict(key=name,case_id=entry['case_id'],repeat=repeat,stage=stage,source_packet=str(path.relative_to(output)),sha256=sha(path)))
    price=dict(input_rate_cny_per_million='0.80',output_rate_cny_per_million='2.80',checked_on='2026-11-02',
        source='https://www.siliconflow.cn/news/ok5ja7brf921kgf1644yyedz',cache_discounts_assumed=False)
    per_request=(Decimal(60000+4096)*Decimal('.8')+Decimal(4096+128)*Decimal('2.8'))/Decimal(1000000)
    proposal=dict(schema='v3-new-development-delivery-pilot-v1',state='pending_user_authorization',grant_message=None,
        provider='SiliconFlow China',endpoint='https://api.siliconflow.cn/v1/chat/completions',model='zai-org/GLM-5.3-Flash',
        dataset_sha256=sha(dataset/'dataset.json'),case_ids=[e['case_id'] for e,_ in selected],repeats=2,max_case_runs=12,
        max_phase_jobs=36,max_requests=72,max_requests_per_phase=2,max_requests_per_case_run=6,max_input_bytes=60000,
        max_tokens=4096,thinking_budget=128,temperature=.2,max_case_estimated_cny='.50',
        max_estimated_cny='6',project_limit_cny='34',project_before_pilot_committed_cny='27.6490780',
        price=price,max_request_reservation_cny=str(per_request),all_known_usage_reservation_upper_cny=str(per_request*72),
        all_case_allowances_cny='6.00',largest_preflight_context_bound_bytes=max(bounds),automatic_retries=0,
        purpose='New-development two-stage delivery reliability pilot only; NOT Gate 3 formal value comparison, NOT memory experiment',
        sent_materials=['Six new entirely authored simulated alert/transaction/authentication/customer/procedure packets at current T0/T1/T2',
                        'Source-bound schemas, model outputs and protocol feedback; no reference answers'],
        excluded_materials=['Old90 cases','40 evaluation candidates','controller references','IBM/ULB transactions','LongMemEval','public complaint narratives','credentials'],
        independent_person_reviewed=False,formal_test_started=False,
        execution_prerequisites=['Offline source and protocol checks','Explicit new grant','Shared-project financial integration separately checked',
                                 'Source/prompt/reference/code freeze and original ledger binding before first live request'])
    write_new(output/'proposal.json',proposal);write_new(output/'proposal-sha256.json',dict(sha256=sha(output/'proposal.json')))
    write_new(output/'prepared.json',dict(jobs=jobs,external_model_requests=0,formal_evaluation=False))
    return proposal


class OfflineDeliveryProbe:
    simulation=True
    def __init__(self,target):self.target=target;self.calls=0
    def chat(self,messages,tools=None):
        self.calls+=1;stage=load_content(messages[0])['stage']
        answer=dict(facts=[]) if stage=='extract' else dict(
            investigation=dict(fact_ids=[],hypotheses=[dict(description='Offline protocol probe, not a model judgment',status='open',support=[],counterevidence=[],unknowns=['Not evaluated'])],
                tensions=[],timeline_refs=[],recommendations=[dict(action='analyst_review',fact_ids=[],reason='Probe only')],missing=['confirmed_case_outcome','customer_statement'],
                stop_reason='unavailable_evidence',summary='Offline probe; business quality intentionally not scored'),
            source_statement=dict(transaction_id=self.target,value='unresolved',fact_ids=[],rationale='Probe only'))
        return dict(role='assistant',content=json.dumps(answer))
    def snapshot(self):return dict(simulated=True,external_model_requests=0,requests=self.calls,estimated_cny='0')


def load_content(message):return json.loads(message['content'])


def probe(directory,output):
    directory,output=Path(directory),Path(output)
    if output.exists():raise FileExistsError('New offline probe output required')
    output.mkdir(parents=True);store=HarnessStore(output/'jobs.sqlite');runs=[]
    for spec in load(directory/'prepared.json')['jobs']:
        path=directory/spec['source_packet']
        if path.resolve().parent!=(directory/'source-packets').resolve() or sha(path)!=spec['sha256']:raise ValueError('Prepared source packet changed')
        packet=load(path);ledger=ledger_for(packet);client=OfflineDeliveryProbe(packet['target_id'])
        job,_=store.submit(spec['key'],dict(mode='offline',case_id=packet['case_id'],question=dict(text='Offline delivery preflight')))
        claim=store.claim('offline-pilot-probe',deadline_seconds=180,mode='offline')
        if claim['id']!=job:raise ValueError('Unexpected offline queue order')
        report=StagedDelivery(store,claim,ledger,client,max_requests=2,report_contract=TaskReportContract(packet['target_id'])).run()
        store.finish(job,claim['owner'],claim['generation'],'awaiting_review',result=report)
        runs.append(dict(case_id=spec['case_id'],repeat=spec['repeat'],stage=spec['stage'],execution_kind='offline_protocol_probe',probe_requests=client.calls))
    receipt=dict(probe_phase_jobs=len(runs),probe_requests=sum(r['probe_requests'] for r in runs),external_model_requests=0,
        model_phase_jobs=0,business_quality_scored=False,trial_authorized=False,runs=runs)
    write_new(output/'probe-receipt.json',receipt);return receipt


def freeze(directory,dataset,*,project_ledger_path=None):
    """Preparation is free; a freeze is not an approval or a live execution."""
    directory,dataset=Path(directory),Path(dataset)
    if (directory/'freeze.json').exists():raise FileExistsError('Preserve the first pilot freeze')
    proposal=load(directory/'proposal.json');manifest=load(dataset/'dataset.json')
    if sha(directory/'proposal.json')!=load(directory/'proposal-sha256.json')['sha256'] or sha(dataset/'dataset.json')!=proposal['dataset_sha256']:raise ValueError('Proposal/dataset changed')
    references={};runtimes={}
    for entry in manifest['entries']:
        if entry['case_id'] not in proposal['case_ids']:continue
        if entry['fold']!='development':raise ValueError('This grant cannot use held-out evaluation cases')
        for field,hash_field in [('runtime','runtime_sha256'),('reference','reference_sha256')]:
            if sha(dataset/entry[field])!=entry[hash_field]:raise ValueError('Dataset artifact changed')
        references[entry['case_id']]=dict(path=str((dataset/entry['reference']).resolve()),sha256=entry['reference_sha256'])
        runtimes[entry['case_id']]=load(dataset/entry['runtime'])
    prepared=load(directory/'prepared.json');specs=prepared['jobs']
    if len(specs)!=36 or len({s['key'] for s in specs})!=36:raise ValueError('36 unique planned phase jobs required')
    expected_keys={(c,r,s) for c in proposal['case_ids'] for r in (1,2) for s in range(3)}
    if {(s['case_id'],s['repeat'],s['stage']) for s in specs}!=expected_keys:raise ValueError('Prepared case/repeat/stage scope changed')
    for spec in specs:
        runtime=runtimes[spec['case_id']];world=InvestigationWorld(runtime)
        for stage in range(1,spec['stage']+1):world.advance(stage)
        bodies=[world.overview(),world.read(sorted(world.eligible()))]
        expected=dict(case_id=spec['case_id'],repeat=spec['repeat'],stage=spec['stage'],cutoff=runtime['cutoff'],as_of=runtime['stage_times'][spec['stage']],
            subject_id=runtime['subject_id'],target_id=runtime['alert']['transaction_id'],transaction_ids=[r['transaction_id'] for r in runtime['transactions']],
            evidence={f'E{i}':dict(result=b,sha256=digest(b)) for i,b in enumerate(bodies,1)})
        path=directory/spec['source_packet']
        if path.resolve().parent!=(directory/'source-packets').resolve() or sha(path)!=spec['sha256'] or load(path)!=expected:raise ValueError('Source packet differs from approved runtime projection')
        if byte_bound(ledger_for(expected),TaskReportContract(expected['target_id']))>proposal['max_input_bytes']:raise ValueError('Request context exceeds approved cap')
    files=[*sorted((ROOT/'app/investigation/v3').glob('*.py')),*sorted((ROOT/'app/investigation').glob('*.py')),
        ROOT/'app/evaluation/v3_delivery_pilot.py',ROOT/'app/evaluation/v3_pilot_ledger.py',
        ROOT/'app/investigation/recording.py',ROOT/'app/investigation/harness_store.py',ROOT/'app/service/store.py',
        ROOT/'app/service/runtime.py',ROOT/'app/llm/siliconflow_client.py',ROOT/'app/llm/cost_ledger.py',ROOT/'app/llm/glm_client.py',
        ROOT/'app/investigation/fraud_world.py',ROOT/'app/investigation/contracts.py']
    policy=load(ROOT/'config/siliconflow-world-v2.json')
    policy.update(max_requests=2,project_budget_cny=proposal['project_limit_cny'],run_budget_cny=proposal['max_case_estimated_cny'],
        max_input_bytes=proposal['max_input_bytes'],request_timeout_seconds=90,run_deadline_seconds=480,
        pricing_checked_on=proposal['price']['checked_on'],pricing_source=proposal['price']['source'])
    files=sorted(set(files))
    for path in files:
        destination=directory/'source'/path.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
    result=dict(schema='v3-delivery-pilot-freeze-v1',proposal_sha256=sha(directory/'proposal.json'),prepared_sha256=sha(directory/'prepared.json'),
        code_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},references=references,policy=policy,
        dependency_versions={n:importlib.metadata.version(n) for n in ('httpx','jsonschema','pydantic')},
        project_ledger_path=str(Path(project_ledger_path or ROOT/'runs/llm-budget.sqlite').resolve()),
        external_model_requests=0,grant_required=True,formal_evaluation=False)
    write_new(directory/'freeze.json',result);write_new(directory/'freeze-sha256.json',dict(sha256=sha(directory/'freeze.json')))
    return result


def checked_freeze(directory):
    frozen=load(directory/'freeze.json')
    if sha(directory/'freeze.json')!=load(directory/'freeze-sha256.json')['sha256'] or sha(directory/'proposal.json')!=frozen['proposal_sha256'] or sha(directory/'prepared.json')!=frozen['prepared_sha256']:raise ValueError('Frozen scope changed')
    if any(sha(ROOT/path)!=expected for path,expected in frozen['code_sha256'].items()):raise ValueError('Frozen execution code changed')
    if any(sha(directory/'source'/path)!=expected for path,expected in frozen['code_sha256'].items()):raise ValueError('Frozen source archive changed')
    if any(importlib.metadata.version(n)!=v for n,v in frozen['dependency_versions'].items()):raise ValueError('Frozen dependencies changed')
    if any(sha(r['path'])!=r['sha256'] for r in frozen['references'].values()):raise ValueError('Frozen author reference changed')
    return frozen


def failure_family(error):
    if not error:return None
    if any(t in error for t in ('budget','request_limit','case_limit','round_limit','project_limit','input_byte_limit','spend_limit')):return 'budget'
    if error in ('source_scope_or_hash','source_missing','future_original','original_subject_or_transaction','transaction_scope','transaction_fields','target_scope','truth_in_runtime','read_hash_changed') or error.startswith('immutable_'):return 'source'
    if error in ('delivery_snapshot_changed','extraction_scope_changed','extraction_changed','terminal_delivery_failure','LeaseLost','task_deadline','heartbeat_shutdown_incomplete','unresolved_call','deadline_or_recovery_limit'):return 'execution'
    if error.startswith(('transport_','http_','usage_','returned_model_','invalid_response_','reported_output_')) or error=='Assistant message has no content or tool calls':return 'provider'
    if error.startswith(('model_','unacquired_','unknown_fact','source_role','fact_transaction_scope','duplicate_fact','hypothesis_','recommendation_','tension_','statement_','unsupported_','outcome_not_','unexpected_tool_')):return 'model_protocol'
    return 'execution'


def execute(directory,*,live=False,client_factory=None):
    """Live path requires the NEW grant; injection is explicitly a transport test.

    No CLI or UI calls this automatically. Unknown fees stop the batch, completed
    phases are immutable, and the ledger prohibits retry of a terminal phase.
    """
    if not live:raise ValueError('Explicit live flag and separate new authorization required')
    from app.evaluation.v3_pilot_ledger import PilotLedger
    from app.service.store import LeaseLost
    from app.investigation.v3.output import OutputFailure
    directory=Path(directory);frozen=checked_freeze(directory);proposal=load(directory/'proposal.json')
    grant=load(directory/'authorization.json')  # Never create or infer this from old grants.
    ledger=PilotLedger(Path(frozen['project_ledger_path']),directory/'proposal.json',grant)
    if client_factory is None:
        from app.cli.investigate_live import local_key
        from app.llm.siliconflow_client import SiliconFlowClient
        key=local_key()
        if not key:raise ValueError('Local credential unavailable; no requests sent')
        client_factory=lambda run_id:SiliconFlowClient(key,policy=frozen['policy'],ledger=ledger,run_id=run_id)
        kind='configured_external_client'
    else:
        supplied=client_factory;client_factory=lambda run_id:supplied(ledger,run_id,frozen['policy'])
        kind='injected_transport_test_NOT_model_evidence'
    live_directory=directory/'live';live_directory.mkdir(exist_ok=True);results_directory=live_directory/'results';results_directory.mkdir(exist_ok=True)
    store=HarnessStore(live_directory/'jobs.sqlite');results=[];stop_reason=None
    for spec in load(directory/'prepared.json')['jobs']:
        checked_freeze(directory)
        path=directory/spec['source_packet']
        if path.resolve().parent!=(directory/'source-packets').resolve() or sha(path)!=spec['sha256']:raise ValueError('Frozen source packet changed')
        result_path=results_directory/(spec['key']+'.json')
        if result_path.exists():results.append(load(result_path));continue
        packet=load(path);run_id=ledger.run_id(spec['case_id'],spec['repeat'],spec['stage'])
        # Do not turn an unknown charged request into failures of subsequent jobs.
        with sqlite3.connect(ledger.path) as db:
            uncertain=db.execute("SELECT id FROM reservations WHERE run_id LIKE ? AND status!='estimated_from_usage'",(ledger.prefix+'%',)).fetchall()
            for (identity,) in uncertain:
                if not db.execute('SELECT 1 FROM v3_pilot_reviews WHERE reservation_id=?',(identity,)).fetchone():
                    stop_reason='uncertain_pilot_request_requires_review'
        if stop_reason:break
        job,_=store.submit(spec['key'],dict(mode='live',case_id=spec['case_id'],question=dict(text='New development delivery pilot'),
            source_packet_sha256=spec['sha256'],proposal_sha256=frozen['proposal_sha256']))
        existing=store.get(job)
        if existing['state']=='running' and existing['lease_until']>store.clock():raise ValueError('An active worker owns this phase')
        claim=store.claim('v3-delivery-pilot',lease_seconds=120,deadline_seconds=480,mode='live') if existing['state'] in ('queued','running') else None
        if existing['state']=='awaiting_review' and existing['result'] is not None:
            if stored_result_digest(existing['result'])!=existing['result_hash']:raise ValueError('Completed report hash changed')
            report=existing['result'];error=None;outcome='delivered';client=None;started=time.monotonic()
        elif claim is None:
            row=store.get(job)
            if row['state']=='running':raise ValueError('Uncertain worker state; no new request sent')
            report=None;error=row['reason'] or row['state'];outcome=ledger.phase_outcome(run_id) or 'execution_failure'
            client=None;started=time.monotonic()
        else:
            if claim['id']!=job:raise ValueError('Unexpected queued phase order')
            client=None;started=time.monotonic();report=None;error=None;outcome='model_failure'
            try:
                client=client_factory(run_id)
                report=StagedDelivery(store,claim,ledger_for(packet),client,max_requests=2,report_contract=TaskReportContract(packet['target_id'])).run()
                store.finish(job,claim['owner'],claim['generation'],'awaiting_review',result=report);outcome='delivered'
            except (KeyboardInterrupt,SystemExit):
                try:store.finish(job,claim['owner'],claim['generation'],'blocked',reason='operator_interrupted')
                except LeaseLost:pass
                ledger.close_phase(run_id,'blocked_unknown')
                raise
            except Exception as exc:
                error=exc.code if isinstance(exc,OutputFailure) else str(exc)[:160] if type(exc).__name__ in ('BudgetExceeded','LLMError','ProtocolError','Blocked') else type(exc).__name__
                outcome={'budget':'budget_failure','source':'source_failure','execution':'execution_failure','provider':'provider_failure','model_protocol':'model_protocol_failure'}[failure_family(error)]
                try:store.finish(job,claim['owner'],claim['generation'],'failed',reason=error)
                except LeaseLost:
                    row=store.get(job)
                    if row['state']=='running' and row['lease_until']<=store.clock():
                        if store.claim('fence-expired-only',mode='live') is not None:raise ValueError('Unexpected queued job while fencing')
                    if store.get(job)['state']=='running':raise ValueError('In-flight owner changed; original response remains archived')
                if client is None:stop_reason='client_initialization_failed'
            finally:
                if hasattr(client,'close'):client.close()
        ledger.close_phase(run_id,outcome)
        reference=load(frozen['references'][spec['case_id']]['path'])['stages'][spec['stage']]
        with sqlite3.connect(ledger.path) as db:
            request_count=db.execute('SELECT COUNT(*) FROM reservations WHERE run_id=?',(run_id,)).fetchone()[0]
        correct=bool(report and report['source_statement']['value']==reference['expected_source_statement']['value'])
        row=store.get(job);state=store.load(job) or {}
        result=dict(case_id=spec['case_id'],repeat=spec['repeat'],stage=spec['stage'],execution_kind=kind,
            delivered=bool(report),source_statement_correct=correct,author_reference_not_external_expert=True,
            error=error,error_family=failure_family(error),recorded_request_reservations=request_count,external_model_requests=request_count if kind=='configured_external_client' else 0,
            elapsed_seconds=round(max(0,row['updated']-row['created']),3),recovery_generations=max(0,row['generation']-1),
            replayed_responses=state.get('replayed_responses',0),report=report,ledger=ledger.summary(run_id))
        write_new(result_path,result);results.append(result)
        if stop_reason:break
        if result['ledger']['unknown_cost_requests']:
            stop_reason='uncertain_pilot_request_requires_review';break
    later=[r for r in results if r['stage'] in (1,2)]
    complete_cases=sum(all(any(r['case_id']==case and r['repeat']==repeat and r['stage']==stage and r['delivered'] for r in results) for stage in range(3)) for case in proposal['case_ids'] for repeat in (1,2))
    summary=dict(complete=len(results)==36,planned_phase_jobs=36,recorded_phase_jobs=len(results),execution_kind=kind,
        external_model_requests=sum(r['external_model_requests'] for r in results),delivered=sum(r['delivered'] for r in results),
        source_statement_correct_T1_T2=sum(r['source_statement_correct'] for r in later),planned_T1_T2_denominator=24,complete_delivered_case_runs=complete_cases,
        delivery_rate_planned=sum(r['delivered'] for r in results)/36,
        source_correct_given_delivery_T1_T2=(sum(r['source_statement_correct'] for r in later)/sum(r['delivered'] for r in later) if any(r['delivered'] for r in later) else None),
        development_gate_only=(dict(delivery_at_least_33_of_36=sum(r['delivered'] for r in results)>=33,
            complete_cases_at_least_10_of_12=complete_cases>=10,current_statement_at_least_20_of_24=sum(r['source_statement_correct'] for r in later)>=20)
            if len(results)==36 and kind=='configured_external_client' else None),
        stop_reason=stop_reason,formal_agent_value_claim=False,results=results)
    financial=ledger.summary(ledger.run_id(proposal['case_ids'][0],1,0))
    summary['financial_snapshot']={k:financial[k] for k in ('batch_committed_estimate_cny','batch_limit_cny','project_committed_estimate_cny','project_additional_risk_hold_cny','validated_project_limit_cny')}
    summary['model_selected_tool_calls']=0  # Full current-material delivery pilot, not planning.
    from app.service.runtime import write_json
    write_new(live_directory/f"summary-{len(list(live_directory.glob('summary-*.json')))+1:03}.json",summary)
    write_json(live_directory/'summary.json',summary)
    return summary
