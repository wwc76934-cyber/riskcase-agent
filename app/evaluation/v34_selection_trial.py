"""New frozen candidate; reduced deterministic duplication, no inherited grant."""
from pathlib import Path
from decimal import Decimal
import shutil
from app.evaluation.v33_schema_trial import prepare as prior_prepare
from app.evaluation.v31_trial import bound
from app.evaluation.v3_typed_repair import load,save,sha
from app.investigation.v3.selected_fact_report import SelectedFactReportContract
from app.investigation.v3.schema_client import delivery_format
from app.investigation.v3.rule_workflow import collect_rule_workflow
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess

def prepare(parent,dataset,output,book):
    output=Path(output)
    if output.exists():raise FileExistsError('New selected-fact proposal required')
    intermediate=output.with_name(output.name+'-input-preflight')
    prior_prepare(parent,dataset,intermediate,book)
    prepared=load(intermediate/'prepared.json');bounds=[];store=JobStore(intermediate/'selection-preflight.sqlite')
    for spec in prepared['cases']:
        runtime=load(intermediate/spec['runtime'])
        store.submit(spec['case_id'],dict(mode='offline',case_id=spec['case_id'],question=dict(text='Selected-fact wire bound')))
        claim=store.claim('selection-preflight',lease_seconds=120,deadline_seconds=600,mode='offline')
        source=SourceAccess(store.path,claim,runtime)
        for stage in range(3):
            if stage:source.advance(stage)
            bounds.append(bound(collect_rule_workflow(source),report_contract_factory=SelectedFactReportContract,response_format_factory=delivery_format))
    if max(bounds)>24000:raise ValueError('New selection contract exceeds declared wire profile')
    p=load(intermediate/'proposal.json');p.update(schema='v34-selected-fact-development-v1',
        delivery_contract=SelectedFactReportContract.name,credential_preflight='v31-negative-control-auth-v1',
        largest_preflight_context_bound_bytes=max(bounds),
        purpose='NEW selected customer-fact development trial; same observed cases; no Agent or memory value claim',
        credential_probe_scope='Two GETs to one newly generated nonexistent batch ID; project credential and invalid control; no listing or response-body read',
        financial_scope='NEW explicit 3 CNY grant required; project 34 CNY; no unused prior authority')
    if Decimal(p['project_before_pilot_committed_cny'])+Decimal('2.969888')>Decimal('34'):
        raise ValueError('Insufficient headroom for complete known profile and one unknown stop')
    output.mkdir()
    for name in ('runtime','controller','source'):shutil.copytree(intermediate/name,output/name)
    save(output/'prepared.json',prepared);save(output/'proposal.json',p)
    freeze=load(intermediate/'freeze.json');freeze['proposal_sha256']=sha(output/'proposal.json')
    save(output/'freeze.json',freeze);save(output/'freeze-sha256.json',dict(sha256=sha(output/'freeze.json')))
    return p
