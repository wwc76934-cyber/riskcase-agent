"""New pending schema-transport trial; old grants never authorize it."""
from pathlib import Path
from decimal import Decimal
import shutil
from app.evaluation.v31_trial import prepare as prior_prepare,bound
from app.evaluation.v3_typed_repair import ROOT,load,save,sha
from app.investigation.v3.report_plan import ReportPlanContract
from app.investigation.v3.schema_client import delivery_format
from app.investigation.v3.rule_workflow import collect_rule_workflow
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess

def prepare(parent,dataset,output,book):
    output=Path(output)
    if output.exists():raise FileExistsError('New proposal directory required')
    intermediate=output.with_name(output.name+'-input-preflight')
    prior_prepare(parent,dataset,intermediate,book,report_contract_factory=ReportPlanContract)
    prepared=load(intermediate/'prepared.json');bounds=[]
    store=JobStore(intermediate/'schema-preflight.sqlite')
    for case in prepared['cases']:
        snapshot=load(intermediate/case['runtime'])
        identity,_=store.submit('schema-'+case['case_id'],dict(mode='offline',case_id=case['case_id'],question=dict(text='Wire bound only')))
        claim=store.claim('schema-preflight',lease_seconds=120,deadline_seconds=600,mode='offline')
        source=SourceAccess(store.path,claim,snapshot)
        for stage in range(3):
            if stage:source.advance(stage)
            bounds.append(bound(collect_rule_workflow(source),report_contract_factory=ReportPlanContract,response_format_factory=delivery_format))
    if max(bounds)>24000:raise ValueError('Schema wire exceeds independently declared input bound')
    p=load(intermediate/'proposal.json');p.update(schema='v33-schema-development-v1',
        transport_contract='v33-strict-json-schema-v1',max_input_bytes=24000,max_request_reservation_cny='0.034304',
        known_usage_request_upper_cny='2.469888',known_usage_plus_one_full_unknown_case_upper_cny='2.969888',
        largest_preflight_context_bound_bytes=max(bounds),
        purpose='New provider JSON Schema transport development trial; NOT independent business or memory value',
        financial_scope='NEW explicit 3 CNY grant; project remains 34; no unused prior authority',
        schema_support_verified_with_provider=False,
        unsupported_schema_policy='Preserve provider rejection, retain unknown charge and stop; never automatically downgrade or retry')
    if Decimal(p['project_before_pilot_committed_cny'])+Decimal('2.969888')>Decimal('34'):
        raise ValueError('Insufficient project headroom')
    output.mkdir()
    for name in ('runtime','controller','source'):shutil.copytree(intermediate/name,output/name)
    save(output/'prepared.json',prepared);save(output/'proposal.json',p)
    freeze=load(intermediate/'freeze.json');freeze['proposal_sha256']=sha(output/'proposal.json')
    save(output/'freeze.json',freeze);save(output/'freeze-sha256.json',dict(sha256=sha(output/'freeze.json')))
    return p
