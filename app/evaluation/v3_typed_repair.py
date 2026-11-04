"""Prepare an explicitly additional, same-round repair scope. No implicit grant."""
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import hashlib,json,sqlite3,shutil,importlib.metadata
from app.investigation.contracts import digest
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess
from app.investigation.v3.rule_workflow import collect_rule_workflow
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.delivery import request_for

ROOT=Path(__file__).resolve().parents[2]
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def request_bound(ledger):
    contract=TypedReviewContract(ledger.target_id);schema=contract.extraction_schema(ledger);facts=[]
    for branch in schema['properties']['facts']['items'].get('anyOf',[]):
        refs=branch['properties']['refs']['items']['enum'];refs=sorted(refs,key=lambda h:len(json.dumps(ledger.lines[h],ensure_ascii=False).encode()),reverse=True)[:3]
        facts.append(dict(predicate=branch['properties']['predicate']['const'],value='unresolved',refs=refs))
    extraction=contract.extract(dict(facts=facts),ledger)
    requests=[request_for('extract',schema,ledger.model_context(),rules=contract.rules_for('extract')),
        request_for('report',contract.schema_for(extraction,ledger),contract.context_for(extraction,ledger),rules=contract.rules_for('report'))]
    return max(len(json.dumps(r,ensure_ascii=False).encode())+1024 for r in requests)


def prepare(parent,dataset,output,project_ledger):
    parent,dataset,output,project_ledger=map(Path,(parent,dataset,output,project_ledger))
    if output.exists():raise FileExistsError('New repair proposal required')
    original=load(parent/'proposal.json');authorization=load(parent/'authorization.json');parent_freeze=load(parent/'freeze.json');completed=load(parent/'live/summary.json')
    if original['schema']!='v3-new-development-delivery-pilot-v1' or not completed['complete'] or completed['recorded_phase_jobs']!=36:raise ValueError('Completed original development round required')
    if sha(parent/'proposal.json')!=authorization['proposal_sha256']:raise ValueError('Original grant changed')
    parent_identity=digest([authorization['proposal_sha256'],authorization]);parent_prefix='v3-delivery-'+parent_identity+':'
    with sqlite3.connect(project_ledger.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT * FROM reservations WHERE run_id LIKE ? ORDER BY id',(parent_prefix+'%',)).fetchall()
        if not rows or any(r[4]!='estimated_from_usage' for r in rows):raise ValueError('Original usage must be known before a repair scope')
        if len(rows)!=completed['external_model_requests']:raise ValueError('Parent model request receipts disagree')
        from app.llm.cost_ledger import CostLedger
        baseline=CostLedger.committed(db.execute('SELECT reserved_cny,estimated_cny FROM reservations').fetchall())+CostLedger.holds(db)
    manifest=load(dataset/'dataset.json')
    if sha(dataset/'dataset.json')!=load(dataset/'dataset-sha256.json')['sha256']:raise ValueError('Candidate data changed')
    output.mkdir(parents=True);(output/'runtime').mkdir();(output/'controller').mkdir();store=JobStore(output/'offline-source-preflight.sqlite');cases=[];bounds=[]
    for case_id in original['case_ids']:
        entry=next(e for e in manifest['entries'] if e['case_id']==case_id and e['fold']=='development')
        runtime_path=dataset/entry['runtime'];reference_path=dataset/entry['reference'];runtime=load(runtime_path);reference=load(reference_path)
        old_reference=Path(parent_freeze['references'][case_id]['path']);old_runtime=old_reference.parent.parent/'runtime'/(case_id+'.json')
        if sha(runtime_path)!=sha(old_runtime) or any(r['cost_units'] for r in runtime['records']):raise ValueError('Repair must keep the same six zero-cost original packets')
        if [s['expected_source_statement'] for s in reference['stages']]!=[s['expected_source_statement'] for s in load(old_reference)['stages']]:raise ValueError('Do not change the primary source-statement labels')
        destination=output/'runtime'/(case_id+'.json');shutil.copyfile(runtime_path,destination);shutil.copyfile(reference_path,output/'controller'/(case_id+'.json'))
        identity,_=store.submit(case_id,dict(mode='offline',case_id=case_id,question=dict(text='Protocol byte bound only')))
        claim=store.claim('free-preflight',lease_seconds=120,deadline_seconds=600,mode='offline');source=SourceAccess(store.path,claim,runtime)
        for stage in range(3):
            if stage:source.advance(stage)
            bound=request_bound(collect_rule_workflow(source));bounds.append(bound)
            if bound>20000:raise ValueError('Typed context exceeds proposed 20000 byte limit; no external call made')
        cases.append(dict(case_id=case_id,runtime='runtime/'+case_id+'.json',runtime_sha256=sha(destination),reference='controller/'+case_id+'.json',reference_sha256=sha(output/'controller'/(case_id+'.json'))))
    maximum=(Decimal(20000+4096)*Decimal('.8')+Decimal(4096+128)*Decimal('2.8'))/Decimal(1000000)
    proposal=dict(schema='v3-typed-repair-amendment-v1',state='pending_user_authorization',grant_message=None,
        model='zai-org/GLM-5.3-Flash',provider='SiliconFlow China',endpoint='https://api.siliconflow.cn/v1/chat/completions',
        case_ids=original['case_ids'],repeats=1,max_case_runs=6,max_phase_jobs=18,max_requests=36,max_requests_per_phase=2,max_requests_per_case_run=6,
        max_input_bytes=20000,max_tokens=4096,thinking_budget=128,temperature=.2,max_case_estimated_cny='.50',
        max_estimated_cny='6',whole_round_limit_cny='6',project_limit_cny='34',project_before_pilot_committed_cny=str(baseline),
        max_request_reservation_cny=str(maximum),known_usage_request_upper_cny=str(maximum*36),additional_case_allowances_cny='3.00',largest_preflight_context_bound_bytes=max(bounds),
        cumulative_count_limits=dict(case_runs=18,phase_jobs=54,requests=108),original_actual_requests=completed['external_model_requests'],
        actual_requests_after_repair_at_most=completed['external_model_requests']+36,automatic_retries=0,
        purpose='Same observed six-case development repair only, not an independent test or Gate 3 comparison',
        price=original['price'],dataset_sha256=sha(dataset/'dataset.json'),source_transport='official_mcp_stdio',condition='B1_staged',
        parent_scope=dict(identity=parent_identity,proposal_path=str((parent/'proposal.json').resolve()),grant_path=str((parent/'authorization.json').resolve()),
            proposal_sha256=sha(parent/'proposal.json'),grant_sha256=sha(parent/'authorization.json'),reservation_rows_sha256=digest([list(r) for r in rows])),
        sent_materials=['The same six authored simulated development runtime originals at their current T0/T1/T2','Typed protocol context and model outputs'],
        excluded_materials=['References and scoring answers','40 evaluation candidates','Old90','IBM/ULB','LongMemEval','Real personal data'],
        thresholds=dict(delivered_at_least=17,complete_cases_at_least=5,source_correct_T1_T2_at_least=10),formal_agent_value_claim=False)
    save(output/'proposal.json',proposal);save(output/'prepared.json',dict(cases=cases,external_model_requests=0))
    files=sorted((ROOT/'app').rglob('*.py'))+[ROOT/'config/siliconflow-world-v2.json']
    for path in files:
        destination=output/'source'/path.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
    frozen=dict(schema='v3-typed-repair-freeze-v1',proposal_sha256=sha(output/'proposal.json'),prepared_sha256=sha(output/'prepared.json'),
        code_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},project_ledger_path=str(project_ledger.resolve()),
        dependency_versions={n:importlib.metadata.version(n) for n in ('mcp','httpx','jsonschema','pydantic')},external_model_requests=0)
    save(output/'freeze.json',frozen);save(output/'freeze-sha256.json',dict(sha256=sha(output/'freeze.json')))
    return proposal


def checked(directory):
    directory=Path(directory);frozen=load(directory/'freeze.json')
    if sha(directory/'freeze.json')!=load(directory/'freeze-sha256.json')['sha256'] or sha(directory/'proposal.json')!=frozen['proposal_sha256'] or sha(directory/'prepared.json')!=frozen['prepared_sha256']:raise ValueError('Frozen repair scope changed')
    current={str(p.relative_to(ROOT)) for p in (ROOT/'app').rglob('*.py')}|{'config\\siliconflow-world-v2.json' if '\\' in next(iter(frozen['code_sha256'])) else 'config/siliconflow-world-v2.json'}
    if current!=set(frozen['code_sha256']):raise ValueError('Frozen repair code inventory changed')
    for relative,expected in frozen['code_sha256'].items():
        if sha(ROOT/relative)!=expected or sha(directory/'source'/relative)!=expected:raise ValueError('Frozen repair code changed')
    if any(importlib.metadata.version(name)!=version for name,version in frozen['dependency_versions'].items()):raise ValueError('Frozen repair dependencies changed')
    for case in load(directory/'prepared.json')['cases']:
        for field in ('runtime','reference'):
            if sha(directory/case[field])!=case[field+'_sha256']:raise ValueError('Frozen repair case changed')
    return frozen


def execute(directory,*,live=False,client_factory=None):
    if not live:raise ValueError('Explicit live invocation and a NEW scope amendment required')
    import asyncio
    from app.evaluation.v3_pilot_ledger import PilotLedger
    from app.investigation.v3.mcp_trajectory import run_case_mcp
    from app.service.store import digest as stored_result_digest
    from app.evaluation.v3_score import score_stage
    directory=Path(directory);frozen=checked(directory);proposal=load(directory/'proposal.json')
    contract_factory=None
    if proposal.get('delivery_contract'):
        from app.investigation.v3.v31_output import V31ReviewContract
        from app.investigation.v3.report_plan import ReportPlanContract
        contracts={c.name:c for c in (V31ReviewContract,ReportPlanContract)}
        if proposal['delivery_contract'] not in contracts:raise ValueError('Unknown frozen delivery contract')
        contract_factory=contracts[proposal['delivery_contract']]
    grant=load(directory/'authorization.json')  # Never infer from remaining money.
    ledger=PilotLedger(Path(frozen['project_ledger_path']),directory/'proposal.json',grant)
    policy=load(ROOT/'config/siliconflow-world-v2.json');policy.update(max_requests=2,max_input_bytes=20000,run_budget_cny='.50',
        project_budget_cny='34',request_timeout_seconds=90,run_deadline_seconds=480,
        pricing_checked_on=proposal['price']['checked_on'],pricing_source=proposal['price']['source'])
    kind='injected_transport_test_NOT_model_evidence' if client_factory else 'separately_authorized_external_client'
    if client_factory is None:
        from app.cli.investigate_live import local_key
        from app.llm.siliconflow_client import SiliconFlowClient
        key=local_key()
        if not key:raise ValueError('Credential unavailable; no requests sent')
        client_factory=lambda scope,run,settings:SiliconFlowClient(key,policy=settings,ledger=scope,run_id=run)
    live_dir=directory/'live';live_dir.mkdir(exist_ok=True);(live_dir/'cases').mkdir(exist_ok=True)
    store=JobStore(live_dir/'jobs.sqlite');results=[];stop=None
    case_specs=[dict(s,repeat=r) for s in load(directory/'prepared.json')['cases'] for r in range(1,proposal['repeats']+1)]
    for spec in case_specs:
        checked(directory);case_id=spec['case_id'];repeat=spec['repeat'];key=case_id+('-r'+str(repeat) if proposal['repeats']>1 else '');result_path=live_dir/'cases'/(key+'.json')
        if result_path.exists():
            saved=load(result_path);results.append(saved)
            if saved['runtime']['blocked_uncertain_request']:stop='recorded_uncertain_execution_requires_review';break
            continue
        runtime=load(directory/spec['runtime'])
        identity,_=store.submit('typed-repair-'+key,dict(mode='live',case_id=case_id,question=dict(text='Observed development repair only'),
            runtime_sha256=spec['runtime_sha256'],proposal_sha256=frozen['proposal_sha256']))
        old=store.get(identity)
        if old['state']=='awaiting_review' and old['result'] is not None:
            if stored_result_digest(old['result'])!=old['result_hash']:raise ValueError('Stored case result changed')
            result=old['result']
        else:
            if old['state']=='running' and old['lease_until']>store.clock():raise ValueError('Active worker owns this repair case')
            claim=store.claim('typed-repair-worker',lease_seconds=120,deadline_seconds=1800,mode='live')
            if not claim or claim['id']!=identity:raise ValueError('Repair case not claimable; no provider request sent')
            source=SourceAccess(store.path,claim,runtime)
            def factory(stage):return client_factory(ledger,ledger.run_id(case_id,repeat,stage),policy)
            async def run():
                with (live_dir/(key+'-mcp.log')).open('a',encoding='utf-8') as log:
                    return await run_case_mcp(source,directory/spec['runtime'],'B1_staged',factory,errlog=log,
                        evidence_kind='separately_authorized_external_client' if kind=='separately_authorized_external_client' else 'scripted_protocol_probe',report_contract_factory=contract_factory)
            run_result=asyncio.run(run())
            # Reference is opened after this case's clients have all closed.
            reference=load(directory/spec['reference']);assessments=[]
            for phase in run_result['phases']:
                stage=phase['stage'];run_id=ledger.run_id(case_id,repeat,stage)
                error_type=(phase.get('error') or {}).get('type')
                outcome='delivered' if phase['report'] else 'blocked_unknown' if phase['blocked_uncertain_request'] else 'source_failure' if error_type=='SourceAccessFailure' else 'model_protocol_failure' if error_type=='OutputFailure' else 'execution_failure'
                ledger.close_phase(run_id,outcome)
                assessment=score_stage(runtime,reference,stage,phase['evidence'],phase['report'])
                if ledger.summary(run_id)['unknown_cost_requests']:ledger.hold_uncertain_case(run_id)
                assessments.append(dict(stage=stage,assessment=assessment,ledger=ledger.summary(run_id)))
            result=dict(case_id=case_id,repeat=repeat,execution_kind=kind,runtime=run_result,assessments=assessments)
            store.finish(identity,claim['owner'],claim['generation'],'awaiting_review',result=result)
        save(result_path,result);results.append(result)
        if result['runtime']['blocked_uncertain_request']:stop='uncertain_execution_requires_review';break
        if any(p['ledger']['unknown_cost_requests'] for p in result['assessments']):stop='uncertain_fee_requires_review';break
    phases=[(r,p) for r in results for p in r['assessments']];later=[p for _,p in phases if p['stage'] in (1,2)]
    delivered=sum(p['assessment']['delivered'] for _,p in phases);correct=sum(p['assessment']['full_information_target_correct'] for p in later)
    complete_cases=sum(r['runtime']['complete'] and all(p['assessment']['delivered'] for p in r['assessments']) for r in results)
    requests=sum(len(p['ledger']['entries']) for _,p in phases)
    financial=ledger.summary(ledger.run_id(proposal['case_ids'][0],1,0))
    thresholds=proposal['thresholds']
    summary=dict(complete=len(results)==len(case_specs) and all(r['runtime']['complete'] for r in results),execution_kind=kind,case_runs_recorded=len(results),
        planned_case_runs=len(case_specs),recorded_phases=len(phases),planned_phases=len(case_specs)*3,delivered=delivered,complete_delivered_cases=complete_cases,
        source_statement_correct_T1_T2=correct,planned_T1_T2_denominator=len(case_specs)*2,recorded_model_request_reservations=requests,
        external_model_requests=requests if kind=='separately_authorized_external_client' else 0,stop_reason=stop,
        development_gate_only=(dict(delivery_threshold_passed=delivered>=thresholds['delivered_at_least'],complete_cases_threshold_passed=complete_cases>=thresholds['complete_cases_at_least'],source_correct_threshold_passed=correct>=thresholds['source_correct_T1_T2_at_least'])
            if len(phases)==len(case_specs)*3 and kind=='separately_authorized_external_client' else None),
        same_observed_cases_not_independent_test=True,formal_agent_value_claim=False,
        financial_snapshot={k:financial[k] for k in ('batch_committed_estimate_cny','project_committed_estimate_cny','project_additional_risk_hold_cny','validated_project_limit_cny')})
    save(live_dir/('summary-'+str(len(list(live_dir.glob('summary-*.json')))+1).zfill(3)+'.json'),summary)
    from app.service.runtime import write_json
    write_json(live_dir/'summary.json',summary)
    return summary
