"""Audited HTTP-401 recovery; never retry a started trajectory or settle its bill.

The original app files, proposal, grant, cases and scoring remain frozen. This
additive controller is separately fingerprinted; it only schedules untouched
case/repeat slots. Missing phases remain failures in the original denominators.
"""
import asyncio
import importlib.metadata
import json
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from app.evaluation.v3_typed_repair import ROOT, load, save, sha
from app.evaluation.v3_pilot_ledger import PilotLedger
from app.investigation.contracts import digest


def frozen(directory):
    directory = Path(directory)
    f = load(directory / 'freeze.json')
    for name, expected in [('freeze.json', load(directory/'freeze-sha256.json')['sha256']),
                           ('proposal.json', f['proposal_sha256']), ('prepared.json', f['prepared_sha256'])]:
        if sha(directory/name) != expected:
            raise ValueError('Original freeze changed')
    expected = set(f['code_sha256'])
    current = {str(p.relative_to(ROOT)) for p in (ROOT/'app').rglob('*.py')}
    current.add(str(Path('config')/'siliconflow-world-v2.json'))
    own = str(Path(__file__).resolve().relative_to(ROOT))
    if current - expected - {own} or expected - current:
        raise ValueError('Unapproved code inventory change')
    for name, fingerprint in f['code_sha256'].items():
        if sha(ROOT/name) != fingerprint or sha(directory/'source'/name) != fingerprint:
            raise ValueError('Original execution/scoring code changed')
    if any(importlib.metadata.version(n) != v for n, v in f['dependency_versions'].items()):
        raise ValueError('Frozen dependencies changed')
    for case in load(directory/'prepared.json')['cases']:
        for field in ('runtime', 'reference'):
            if sha(directory/case[field]) != case[field+'_sha256']:
                raise ValueError('Frozen case changed')
    return f


def inspect_rejection(ledger, result):
    phases = result['runtime']['phases']
    if len(phases) != 1 or phases[0]['stage'] != 0 or not result['runtime']['blocked_uncertain_request']:
        raise ValueError('Only an interrupted first-stage trajectory is eligible')
    phase = phases[0]
    usage = phase['usage']
    audits = usage.get('request_audit', [])
    if (phase.get('report') is not None or len(audits) != 1 or usage.get('requests_sent') != 1
        or audits[0].get('http_status') != 401 or audits[0].get('error_code') != 'http_401'
        or audits[0].get('returned_model') or not audits[0].get('request_trace_id')):
        raise ValueError('Not a definite HTTP authentication rejection without model output')
    run = ledger.run_id(result['case_id'], result['repeat'], 0)
    with closing(sqlite3.connect(ledger.path)) as db:
        row = db.execute('SELECT * FROM reservations WHERE id=?', (audits[0]['reservation_id'],)).fetchone()
        if not row or row[1] != run or row[4] != 'uncertain_reserved' or row[3] is not None:
            raise ValueError('Rejected request reservation does not match')
        case = ledger.parts(run)
        rows = db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id LIKE ?', (case+':%',)).fetchall()
        if len(rows) != 1 or ledger.committed(rows)+ledger.holds(db, prefix=case) != Decimal('.50'):
            raise ValueError('Exactly one attempt and full conservative allowance required')
    if ledger.phase_outcome(run) != 'blocked_unknown':
        raise ValueError('Original failed phase must remain terminal')
    return row


def prepare_review(directory, *, credential_check, operator, user_configuration_message):
    """Local operator review, not a human business review or a billing receipt."""
    directory = Path(directory); f = frozen(directory)
    if credential_check != dict(http_status=200, model_available=True, generation_requests=0):
        raise ValueError('Successful read-only authentication check required')
    if not operator or not user_configuration_message:
        raise ValueError('Named operator and user configuration confirmation required')
    ledger = PilotLedger(Path(f['project_ledger_path']), directory/'proposal.json', load(directory/'authorization.json'))
    files = list((directory/'live/cases').glob('*.json'))
    if len(files) != 1:
        raise ValueError('Recovery only from the single first rejected trajectory')
    result = load(files[0]); row = inspect_rejection(ledger, result)
    review = dict(schema='v33-authentication-recovery-v1', operator=operator,
        user_configuration_message=user_configuration_message, credential_check=credential_check,
        original_freeze_sha256=sha(directory/'freeze.json'), proposal_sha256=sha(directory/'proposal.json'),
        grant_sha256=sha(directory/'authorization.json'), controller_sha256=sha(__file__),
        original_case_path=str(files[0].relative_to(directory)), original_case_sha256=sha(files[0]),
        reservation_id=row[0], reservation_hash=digest(list(row)),
        excluded_case=dict(case_id=result['case_id'], repeat=result['repeat']),
        provider_charge_still_unknown=True, retained_case_allowance_cny='.50',
        automatic_retry=False, only_untouched_case_runs=True, original_denominators=dict(case_runs=12, phases=36, T1_T2=24))
    save(directory/'recovery-review.json', review)
    return review


def checked_review(directory):
    directory = Path(directory); f = frozen(directory); r = load(directory/'recovery-review.json')
    checks = [('freeze.json','original_freeze_sha256'), ('proposal.json','proposal_sha256'),
              ('authorization.json','grant_sha256'), (r['original_case_path'],'original_case_sha256')]
    if any(sha(directory/p) != r[k] for p,k in checks) or sha(__file__) != r['controller_sha256']:
        raise ValueError('Recovery artifacts changed')
    ledger = PilotLedger(Path(f['project_ledger_path']), directory/'proposal.json', load(directory/'authorization.json'))
    row = inspect_rejection(ledger, load(directory/r['original_case_path']))
    if row[0] != r['reservation_id'] or digest(list(row)) != r['reservation_hash']:
        raise ValueError('Original reservation changed')
    # The existing ledger supports individually reviewed unknown charges. Keep
    # the original row and full hold; never label this as resolved or zero cost.
    with closing(sqlite3.connect(ledger.path)) as db, db:
        ledger._authority(db)
        body = json.dumps(r, ensure_ascii=False)
        old = db.execute('SELECT reservation_hash,body FROM v3_pilot_reviews WHERE reservation_id=?', (row[0],)).fetchone()
        expected = (digest(list(row)), body)
        if old and old != expected:
            raise ValueError('Existing individual review differs')
        db.execute('INSERT OR IGNORE INTO v3_pilot_reviews VALUES (?,?,?,?)', (row[0],ledger.identity,*expected))
    return ledger, r


def execute(directory, *, live=False, client_factory=None):
    if not live:
        raise ValueError('Explicit live execution required')
    from app.service.store import JobStore, digest as result_digest
    from app.service.v3_sources import SourceAccess
    from app.investigation.v3.mcp_trajectory import run_case_mcp
    from app.investigation.v3.report_plan import ReportPlanContract
    from app.evaluation.v3_score import score_stage
    directory = Path(directory); ledger, review = checked_review(directory)
    proposal = load(directory/'proposal.json')
    if proposal.get('delivery_contract') != ReportPlanContract.name or proposal.get('transport_contract') != 'v33-strict-json-schema-v1':
        raise ValueError('Frozen coded report contract required')
    policy = load(ROOT/'config/siliconflow-world-v2.json')
    policy.update(max_requests=2, max_input_bytes=24000, run_budget_cny='.50', project_budget_cny='34',
                  request_timeout_seconds=90, run_deadline_seconds=480,
                  pricing_checked_on=proposal['price']['checked_on'], pricing_source=proposal['price']['source'])
    real = client_factory is None
    if real:
        from app.cli.investigate_live import local_key
        from app.investigation.v3.schema_client import SchemaDeliveryClient as SiliconFlowClient
        key = local_key()
        if not key: raise ValueError('Credential unavailable')
        client_factory = lambda scope,run,settings: SiliconFlowClient(key,policy=settings,ledger=scope,run_id=run)
    live_dir = directory/'live'; store = JobStore(live_dir/'jobs.sqlite'); results=[]; stop=None
    specs = [dict(s,repeat=r) for s in load(directory/'prepared.json')['cases'] for r in range(1,3)]
    for spec in specs:
        checked_review(directory)
        case_id, repeat = spec['case_id'], spec['repeat']; case_key=case_id+'-r'+str(repeat)
        path = live_dir/'cases'/(case_key+'.json')
        excluded = dict(case_id=case_id,repeat=repeat) == review['excluded_case']
        if path.exists():
            result = load(path); results.append(result)
            if result['runtime']['blocked_uncertain_request'] and not excluded:
                stop='new_uncertainty_requires_review'; break
            continue
        if excluded: raise ValueError('Original rejected result disappeared')
        identity,_ = store.submit('typed-repair-'+case_key,dict(mode='live',case_id=case_id,
            question=dict(text='Observed development repair only'),runtime_sha256=spec['runtime_sha256'],
            proposal_sha256=sha(directory/'proposal.json')))
        old = store.get(identity)
        if old['state']=='awaiting_review' and old['result'] is not None:
            if result_digest(old['result']) != old['result_hash']: raise ValueError('Stored result changed')
            result=old['result']
        else:
            # A crash after a paid request is not permission to retry that case.
            with closing(sqlite3.connect(ledger.path)) as db:
                attempted=db.execute('SELECT COUNT(*) FROM reservations WHERE run_id LIKE ?',
                    (ledger.parts(ledger.run_id(case_id,repeat,0))+':%',)).fetchone()[0]
            if attempted: raise ValueError('Started trajectory without terminal result requires review')
            claim=store.claim('v31-auth-recovery',lease_seconds=120,deadline_seconds=1800,mode='live')
            if not claim or claim['id']!=identity: raise ValueError('Case not claimable')
            source=SourceAccess(store.path,claim,load(directory/spec['runtime']))
            def factory(stage): return client_factory(ledger,ledger.run_id(case_id,repeat,stage),policy)
            async def run():
                with (live_dir/(case_key+'-mcp.log')).open('a',encoding='utf-8') as log:
                    return await run_case_mcp(source,directory/spec['runtime'],'B1_staged',factory,errlog=log,
                        evidence_kind='separately_authorized_external_client' if real else 'scripted_protocol_probe',
                        report_contract_factory=ReportPlanContract)
            runtime=asyncio.run(run()); assessments=[]
            reference=load(directory/spec['reference'])
            for phase in runtime['phases']:
                stage=phase['stage']; run_id=ledger.run_id(case_id,repeat,stage)
                error_type=(phase.get('error') or {}).get('type')
                outcome=('delivered' if phase['report'] else 'blocked_unknown' if phase['blocked_uncertain_request']
                    else 'source_failure' if error_type=='SourceAccessFailure' else 'model_protocol_failure'
                    if error_type=='OutputFailure' else 'execution_failure')
                ledger.close_phase(run_id,outcome)
                if ledger.summary(run_id)['unknown_cost_requests']: ledger.hold_uncertain_case(run_id)
                assessments.append(dict(stage=stage,assessment=score_stage(load(directory/spec['runtime']),reference,
                    stage,phase['evidence'],phase['report']),ledger=ledger.summary(run_id)))
            result=dict(case_id=case_id,repeat=repeat,execution_kind='separately_authorized_external_client' if real else 'injected_transport_test_NOT_model_evidence',
                        runtime=runtime,assessments=assessments)
            store.finish(identity,claim['owner'],claim['generation'],'awaiting_review',result=result)
        save(path,result); results.append(result)
        print(json.dumps(dict(case_id=case_id,repeat=repeat,delivered=sum(p['assessment']['delivered'] for p in result['assessments']))),flush=True)
        if result['runtime']['blocked_uncertain_request'] or any(p['ledger']['unknown_cost_requests'] for p in result['assessments']):
            stop='new_uncertainty_requires_review'; break
    phases=[p for r in results for p in r['assessments']]
    delivered=sum(p['assessment']['delivered'] for p in phases)
    complete=sum(r['runtime']['complete'] and len(r['assessments'])==3 and all(p['assessment']['delivered'] for p in r['assessments']) for r in results)
    correct=sum(p['assessment']['full_information_target_correct'] for p in phases if p['stage'] in (1,2))
    done=len(results)==12
    summary=dict(scheduled_case_runs_accounted=done,all_phases_executed=len(phases)==36,
        case_runs_recorded=len(results),planned_case_runs=12,recorded_phases=len(phases),planned_phases=36,
        delivered=delivered,complete_delivered_cases=complete,source_statement_correct_T1_T2=correct,
        planned_T1_T2_denominator=24,missing_phases_count_as_unsuccessful=36-len(phases),
        development_thresholds=(dict(delivery_threshold_passed=delivered>=33,complete_cases_threshold_passed=complete>=10,
                                    source_correct_threshold_passed=correct>=20) if done and real else None),
        stop_reason=stop,external_model_requests=sum(len(p['ledger']['entries']) for p in phases) if real else 0,
        original_authentication_failure_retained=True,formal_agent_value_claim=False,
        financial_snapshot=ledger.summary(ledger.run_id(proposal['case_ids'][0],1,0)))
    save(live_dir/('recovery-summary-'+str(len(list(live_dir.glob('recovery-summary-*.json')))+1).zfill(3)+'.json'),summary)
    return summary
