"""Reference-free B0/B1/B2/B4 execution over one case and three arrival stages.

No evaluation imports, credential lookup, enabling policy or paid authorization.
The controller owns those concerns. B3 remains gated until an actual stable agent
baseline and a separately frozen method set exist.
"""
from copy import deepcopy
import json,time
from app.service.store import Blocked,digest as state_digest,encoded
from app.service.v3_phase_store import DeliveryPhase
from app.investigation.contracts import digest as content_digest
from app.investigation.v3.rule_workflow import collect_rule_workflow,collect_free_and_prerequisites,compile_ledger,acquired_ids,checked_call,rule_report,readable_batch
from app.investigation.v3.query_world import CONTRACT as QUERY_SIMULATION
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.hybrid_review import HybridReviewContract
from app.investigation.v3.native_planner import NativePlanner
from app.investigation.v3.delivery import StagedDelivery
from app.investigation.v3.composite_delivery import CompositeDelivery

CONDITIONS={'B0','B1','B1_staged','B2','B4'}


def collect_full_budgeted(source,stage,client,prior):
    context=collect_free_and_prerequisites(source);catalog=context['catalog'];known=acquired_ids(source)
    remaining=[s for s in catalog['sources'] if s['source_id'] not in known and s.get('readable',True)]
    batch=readable_batch(catalog,remaining)
    if remaining and len(batch)==len(remaining) and all(set(s['requires'])<=known for s in remaining) and sum(s['cost_units'] for s in remaining)<=catalog['remaining_cost_units'] and sum(s['latency_minutes'] for s in remaining)<=catalog['remaining_minutes']:
        for start in range(0,len(batch),12):checked_call(source,'v3_read',dict(source_ids=[s['source_id'] for s in batch[start:start+12]]))
        plan=dict(mode='full_current_materials',model_requests=0,trace=[])
    elif remaining:
        plan=NativePlanner(source,stage,client,mode='single_batch',prior_assessments=prior).run()
    else:plan=dict(mode='already_complete',model_requests=0,trace=[])
    context['catalog']=source.position()['catalog']
    return compile_ledger(source,context),plan


def run_case(source,condition,client_factory=None,*,evidence_kind='scripted_protocol_probe',report_contract_factory=None):
    if condition=='B3':raise Blocked('memory_gate_not_ready')
    if condition not in CONDITIONS:raise ValueError('Registered condition required')
    if condition!='B0' and client_factory is None:raise ValueError('Caller must supply a separately authorized client factory')
    if evidence_kind not in ('scripted_protocol_probe','separately_authorized_external_client'):raise ValueError('Declared execution evidence kind')
    binding=content_digest([source.snapshot,condition,evidence_kind,'v3-reference-free-trajectory-v1'])
    if report_contract_factory is not None:
        study_model=source.snapshot['contract']==QUERY_SIMULATION and condition in ('B1','B2','B4')
        if condition!='B1_staged' and not study_model:raise ValueError('Opt-in delivery requires a declared research path')
        binding=content_digest([binding,report_contract_factory.name])
    with source.transaction() as db:
        source._load(db)
        db.execute('CREATE TABLE IF NOT EXISTS v3_case_phases(job_id TEXT NOT NULL,stage INTEGER NOT NULL,binding TEXT NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(job_id,stage))')
        db.execute('CREATE TABLE IF NOT EXISTS v3_case_stage_start(job_id TEXT NOT NULL,stage INTEGER NOT NULL,at REAL NOT NULL,tool_attempts INTEGER NOT NULL,PRIMARY KEY(job_id,stage))')
    phases=[];prior=[];blocked=False
    for stage in range(3):
        with source.transaction() as db:
            source._load(db);old=db.execute('SELECT * FROM v3_case_phases WHERE job_id=? AND stage=?',(source.claim['id'],stage)).fetchone()
        if old:
            row=json.loads(old['body'])
            if old['binding']!=binding or state_digest(row)!=old['hash']:raise Blocked('case_phase_binding_changed')
            phases.append(row)
            if row['report']:prior.append(prior_summary(row))
            if row['blocked_uncertain_request']:blocked=True;break
            continue
        current=source.position()['stage']
        if current<stage:source.advance(stage)
        elif current>stage:raise Blocked('case_stage_skipped_without_record')
        phase=DeliveryPhase(source,stage);saved=phase.load(phase.identity)
        with source.transaction() as db:
            state,_=source._load(db)
            db.execute('INSERT OR IGNORE INTO v3_case_stage_start VALUES (?,?,?,?)',(source.claim['id'],stage,source.clock(),state['attempts']))
            timer=db.execute('SELECT at,tool_attempts FROM v3_case_stage_start WHERE job_id=? AND stage=?',(source.claim['id'],stage)).fetchone()
        report=None;plan=None;error=None;client=None;source_uncertain=False
        try:
            if saved and saved.get('phase')=='done':
                report=deepcopy(saved['report']);plan=dict(recovered_completed_delivery=True)
            elif saved and saved.get('phase')=='failed':
                error=dict(type='saved_terminal_protocol_failure',detail=saved.get('error') or saved.get('protocol_errors'))
            elif condition=='B0':
                report=rule_report(collect_rule_workflow(source),study=source.snapshot['contract']==QUERY_SIMULATION)
                plan=dict(mode='deterministic_workflow',model_requests=0)
            else:
                client=client_factory(stage)
                if condition=='B2':
                    plan=NativePlanner(source,stage,client,prior_assessments=prior).run()
                    context=dict(frame=source.evidence()['E1']['result'],catalog=source.position()['catalog'])
                    ledger=compile_ledger(source,context)
                elif condition=='B4' and source.snapshot['contract']==QUERY_SIMULATION:
                    ledger=collect_rule_workflow(source)
                    plan=dict(mode='deterministic_workflow',model_requests=0)
                else:ledger,plan=collect_full_budgeted(source,stage,client,prior)
                factory=report_contract_factory or (HybridReviewContract if condition=='B4' else TypedReviewContract)
                contract=factory(ledger.target_id)
                if condition=='B1':report=CompositeDelivery(phase,ledger,client,contract).run()
                else:report=StagedDelivery(phase,phase.claim,ledger,client,max_requests=2,report_contract=contract).run()
        except (KeyboardInterrupt,SystemExit):raise
        except Exception as exc:
            error=dict(type=type(exc).__name__,code=getattr(exc,'code',None),reason=str(exc)[:160])
            source_uncertain=getattr(exc,'code',None)=='source_transport_unknown'
        finally:
            usage=client.snapshot() if client is not None and hasattr(client,'snapshot') else None
            if client is not None and hasattr(client,'close'):client.close()
        with source.transaction() as db:
            state,world=source._load(db)
            journal=list(db.execute('SELECT lane,ordinal,state FROM v3_phase_calls WHERE job_id=? AND stage=?',(source.claim['id'],stage)))
            uncertain=db.execute("SELECT 1 FROM v3_phase_calls WHERE job_id=? AND state!='completed'",(source.claim['id'],)).fetchone()
            blocked=bool(uncertain) or source_uncertain
            position=dict(stage=world.stage,as_of=source.snapshot['stage_times'][world.stage],tool_attempts=state['attempts'],
                acquired_source_ids=sorted(world.acquired),catalog=world.catalog())
            evidence={key:deepcopy(state['evidence'][key]) for key in sorted(state['evidence'],key=lambda k:int(k[1:]))}
            row=dict(stage=stage,as_of=position['as_of'],condition=condition,report=report,plan=plan,error=error,
                protocol_request_attempts=len(journal),usage=usage,process_elapsed_seconds_including_recovery=round(max(0,source.clock()-timer['at']),6),
                source_tool_attempts=state['attempts']-timer['tool_attempts'],source_state=position,
                simulated_source_latency_minutes=world.elapsed[stage],evidence=evidence,blocked_uncertain_request=blocked,blocked_uncertain_source=source_uncertain,evidence_kind=evidence_kind)
            db.execute('INSERT INTO v3_case_phases VALUES (?,?,?,?,?)',(source.claim['id'],stage,binding,encoded(row),state_digest(row)))
        phases.append(row)
        if report:prior.append(prior_summary(row))
        if blocked:break
    with source.transaction() as db:
        source._load(db)
        planning_attempts=db.execute("SELECT COUNT(*) FROM v3_phase_calls WHERE job_id=? AND lane='planner'",(source.claim['id'],)).fetchone()[0]
    return dict(case_id=source.snapshot['case_id'],condition=condition,complete=len(phases)==3,phases=phases,
        blocked_uncertain_request=blocked,evidence_kind=evidence_kind,formal_agent_value_claim=False,
        planning_protocol_attempts=planning_attempts)


def prior_summary(row):
    report=row['report']
    return dict(stage=row['stage'],as_of=row['as_of'],source_statement=report['source_statement']['value'],
        proposed_disposition=report.get('proposed_disposition'),hypotheses=[h['description'] for h in report['hypotheses']],
        warning='Prior model/rule assessment, not original evidence; no prior F/V identifiers are valid citations')
