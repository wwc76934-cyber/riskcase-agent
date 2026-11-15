"""Read-only extraction compatibility, never a new delivery/semantic score."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,sqlite3
from app.evaluation.v3_delivery_pilot import load,ledger_for
from app.investigation.v3.task_output import TaskReportContract
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.output import OutputFailure,PREDICATE_ROLES

def convert(payload,ledger):
    """Only remove deterministic display fields; do not drop or repair facts."""
    facts=[]
    if set(payload)!={'facts'}:raise ValueError('Unexpected old extraction envelope')
    for i,old in enumerate(payload['facts'],1):
        if old['id']!='F'+str(i) or old['transaction_ids']!=[ledger.target_id]:raise ValueError('Display/transaction conversion not lossless')
        fact=deepcopy(old);fact.pop('id');fact.pop('transaction_ids')
        if fact['predicate']=='policy_version':
            lines=[ledger.lines[h] for h in fact['refs']]
            if not lines or type(fact['value']) is not int or any(l['source_type']!='procedure' or l['version']!=fact['value'] for l in lines):raise ValueError('Policy prose or mismatched version cannot be converted')
            fact.pop('value')
        facts.append(fact)
    return dict(facts=facts)

def accepts(fn):
    try:fn();return dict(accepted=True,error=None)
    except (OutputFailure,ValueError,KeyError,TypeError) as exc:return dict(accepted=False,error=getattr(exc,'code',type(exc).__name__),
        conversion_reason=str(exc) if type(exc) is ValueError else None)

def report_conversion(payload):
    """Remove duplicate display fields only. Never supply a new disposition."""
    value=deepcopy(payload)
    value['investigation'].pop('fact_ids')
    value['source_statement'].pop('transaction_id')
    # V3.1 adds a business recommendation field. Absence is NOT permission for
    # the host to invent continue_collection, escalation or a close decision.
    return value

def source_diagnostics(payload,ledger,reference,stage):
    """Finite author-reference observations; never full free-prose grading."""
    expected={(f['source_id'],f['predicate']):f['value'] for f in reference['stages'][stage]['expected_observations']}
    rows=[]
    for f in payload['facts']:
        refs=f['refs'];lines=[ledger.lines[h] for h in refs if h in ledger.lines]
        canonical=all((l['source_id'],f['predicate']) in expected for l in lines) and bool(lines)
        rows.append(dict(fact_id=f['id'],predicate=f['predicate'],all_sources_acquired=len(lines)==len(refs),
            source_role_valid=bool(lines) and all(l['source_type'] in PREDICATE_ROLES.get(f['predicate'],[]) for l in lines),
            target_only=f['transaction_ids']==[ledger.target_id],
            source_transaction_scope_valid=bool(lines) and all(set(f['transaction_ids'])<=set(l['transaction_ids']) for l in lines),
            canonical_author_reference_available=canonical,
            matches_canonical_author_value=all(f['value']==expected[(l['source_id'],f['predicate'])] for l in lines) if canonical else None,
            free_prose_semantics_evaluated=False))
    return rows

def full_audit(pilot,dataset,output):
    """Both archived stages, complete planned denominator, no provider access."""
    pilot,dataset,output=Path(pilot),Path(dataset),Path(output)
    if output.exists():raise FileExistsError('New full diagnostic output required')
    database=pilot/'live/jobs.sqlite';before=hashlib.sha256(database.read_bytes()).hexdigest()
    entries={s['key']:s for s in load(pilot/'prepared.json')['jobs']}
    references={e['case_id']:e for e in load(dataset/'dataset.json')['entries']};rows=[]
    from app.investigation.v3.output import checked,extract
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        jobs={key:identity for identity,key in db.execute('SELECT id,request_key FROM jobs')}
        for key,spec in entries.items():
            ledger=ledger_for(load(pilot/spec['source_packet']));old=TaskReportContract(ledger.target_id);new=V31ReviewContract(ledger.target_id)
            row=dict(key=key,stage=spec['stage'],report_present=False,original_delivery_replayed=False,new_report_compatible=False,source_diagnostics=[])
            calls=dict(db.execute("SELECT ordinal,response FROM calls WHERE job_id=? AND state='completed'",(jobs[key],)))
            def parsed(ordinal):return json.loads(json.loads(calls[ordinal])['raw_response']['content'])
            try:payload=parsed(1)
            except (KeyError,ValueError,TypeError):row['parse_error']=True;rows.append(row);continue
            def first():checked(payload,old.extraction_schema(ledger));return extract(payload,ledger)
            row['old_extraction']=accepts(first)
            row['conversion']=accepts(lambda:new.extract(convert(payload,ledger),ledger))
            e=references[spec['case_id']];reference=load(dataset/e['reference'])
            if hashlib.sha256((dataset/e['reference']).read_bytes()).hexdigest()!=e['reference_sha256']:raise ValueError('Author reference changed')
            row['source_diagnostics']=source_diagnostics(payload,ledger,reference,spec['stage'])
            if 2 in calls:
                row['report_present']=True
                try:report=parsed(2)
                except (KeyError,ValueError,TypeError):row['report_parse_error']=True;rows.append(row);continue
                row['old_report']=accepts(lambda:old.assemble(report,first(),ledger))
                row['original_delivery_replayed']=row['old_report']['accepted']
                row['new_report']=accepts(lambda:new.assemble(report_conversion(report),new.extract(convert(payload,ledger),ledger),ledger))
                row['new_report_compatible']=row['new_report']['accepted']
                row['report_conversion_missing_new_fields']=sorted(set(new.schema['required'])-set(report_conversion(report)))
                row['target_state_matches_author_reference']=bool(row['original_delivery_replayed'] and report['source_statement']['transaction_id']==ledger.target_id
                    and report['source_statement']['value']==reference['stages'][spec['stage']]['expected_source_statement']['value'])
            rows.append(row)
    if hashlib.sha256(database.read_bytes()).hexdigest()!=before:raise ValueError('Original database changed')
    later=[r for r in rows if r['stage'] in (1,2)]
    result=dict(scope='Archived extraction and final-report compatibility; author-reference diagnostics, NOT replacement experiment scores',
        planned_stages=len(entries),recorded_stages=len(rows),report_responses=sum(r['report_present'] for r in rows),
        original_deliveries_replayed=sum(r['original_delivery_replayed'] for r in rows),new_final_reports_compatible=sum(r['new_report_compatible'] for r in rows),
        original_target_correct_T1_T2_reproduced=sum(r.get('target_state_matches_author_reference',False) for r in later),planned_T1_T2_denominator=len(later),
        reference_scope='Current author canonical values; source diagnostics are NOT old business-score revisions or expert adjudication',
        original_db_sha256=before,original_db_unchanged=True,new_model_behavior_measured=False,external_model_requests=0,rows=rows)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result

def audit(pilot,output):
    pilot,output=Path(pilot),Path(output)
    if output.exists():raise FileExistsError('Do not overwrite diagnostic or experimental results')
    database=pilot/'live/jobs.sqlite';before=hashlib.sha256(database.read_bytes()).hexdigest()
    prepared={s['key']:s for s in load(pilot/'prepared.json')['jobs']};rows=[]
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for identity,key in db.execute('SELECT id,request_key FROM jobs ORDER BY request_key'):
            raw=db.execute('SELECT response FROM calls WHERE job_id=? AND ordinal=1 AND state=?',(identity,'completed')).fetchone()
            if raw is None:continue
            envelope=json.loads(raw[0]);message=envelope['raw_response'];packet=load(pilot/prepared[key]['source_packet']);ledger=ledger_for(packet)
            try:payload=json.loads(message['content'])
            except (KeyError,TypeError,ValueError):rows.append(dict(key=key,parse=False));continue
            old=TaskReportContract(ledger.target_id);new=V31ReviewContract(ledger.target_id)
            from app.investigation.v3.output import checked,extract
            def original():checked(payload,old.extraction_schema(ledger));extract(payload,ledger)
            rows.append(dict(key=key,parse=True,raw_response_sha256=hashlib.sha256(raw[0].encode()).hexdigest(),
                old_contract=accepts(original),new_wire_without_conversion=accepts(lambda:new.extract(payload,ledger)),
                deterministic_conversion=accepts(lambda:new.extract(convert(payload,ledger),ledger))))
    after=hashlib.sha256(database.read_bytes()).hexdigest()
    if before!=after:raise ValueError('Original database changed')
    summary=dict(scope='First extraction responses only, not final-report or business regrading',extraction_responses=len(rows),parsed_responses=sum(r['parse'] for r in rows),
        old_accepted=sum(r.get('old_contract',{}).get('accepted',False) for r in rows),
        direct_new_wire_accepted=sum(r.get('new_wire_without_conversion',{}).get('accepted',False) for r in rows),
        lossless_conversion_accepted=sum(r.get('deterministic_conversion',{}).get('accepted',False) for r in rows),
        original_db_sha256=before,original_db_unchanged=True,semantic_correctness_evaluated=False,
        new_model_behavior_measured=False,new_delivery_rate_measured=False,external_model_requests=0,rows=rows)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');return summary
