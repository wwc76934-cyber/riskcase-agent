"""A fresh, pending V3.1 development trial. Preparation grants no authority."""
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import shutil,importlib.metadata
from app.evaluation.v3_typed_repair import prepare as old_prepare,load,save,sha,ROOT
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.delivery import request_for
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess
from app.investigation.v3.rule_workflow import collect_rule_workflow

def bound(ledger):
    contract=V31ReviewContract(ledger.target_id);schema=contract.extraction_schema(ledger);facts=[]
    for branch in schema['properties']['facts']['items'].get('anyOf',[]):
        p=branch['properties'];handles=p['refs']['items']['enum'];fact=dict(predicate=p['predicate']['const'],refs=handles[:3])
        if 'value' in p:fact['value']=max(p['value']['enum'],key=len)
        facts.append(fact)
    extraction=contract.extract(dict(facts=facts),ledger)
    import json
    requests=[request_for('extract',schema,ledger.model_context(),rules=contract.rules_for('extract')),
        request_for('report',contract.schema_for(extraction,ledger),contract.context_for(extraction,ledger),rules=contract.rules_for('report'))]
    return max(len(json.dumps(r,ensure_ascii=False).encode())+1024 for r in requests)

def prepare(parent,dataset,output,book):
    output=Path(output)
    if output.exists():raise FileExistsError('New trial scope required')
    intermediate=output.with_name(output.name+'-input-preflight')
    prior=old_prepare(parent,dataset,intermediate,book)
    output.mkdir(parents=True)
    for directory in ('runtime','controller'):shutil.copytree(intermediate/directory,output/directory)
    prepared=load(intermediate/'prepared.json');save(output/'prepared.json',prepared)
    store=JobStore(output/'offline-preflight.sqlite');bounds=[]
    for case in prepared['cases']:
        snapshot=load(output/case['runtime']);identity,_=store.submit(case['case_id'],dict(mode='offline',case_id=case['case_id'],question=dict(text='Schema bound only')))
        claim=store.claim('v31-preflight',lease_seconds=120,deadline_seconds=600,mode='offline');source=SourceAccess(store.path,claim,snapshot)
        for stage in range(3):
            if stage:source.advance(stage)
            bounds.append(bound(collect_rule_workflow(source)))
    if max(bounds)>20000:raise ValueError('V3.1 protocol exceeds declared request envelope')
    proposal=deepcopy(prior);proposal.pop('parent_scope')
    for key in ('whole_round_limit_cny','cumulative_count_limits','original_actual_requests','actual_requests_after_repair_at_most'):proposal.pop(key,None)
    proposal.update(schema='v3-new-development-delivery-pilot-v1',delivery_contract=V31ReviewContract.name,
        repeats=2,max_case_runs=12,max_phase_jobs=36,max_requests=72,known_usage_request_upper_cny=str(Decimal('0.031104')*72),
        additional_case_allowances_cny='6.00',largest_preflight_context_bound_bytes=max(bounds),
        purpose='Fresh V3.1 observed-development reliability trial, NOT independent business value or memory',
        thresholds=dict(delivered_at_least=33,complete_cases_at_least=10,source_correct_T1_T2_at_least=20),
        financial_scope='NEW round, max estimated 6 CNY; project 34 CNY; requires a new explicit user grant')
    save(output/'proposal.json',proposal)
    files=sorted((ROOT/'app').rglob('*.py'))+[ROOT/'config/siliconflow-world-v2.json']
    for path in files:
        destination=output/'source'/path.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
    save(output/'freeze.json',dict(schema='v31-development-trial-freeze-v1',proposal_sha256=sha(output/'proposal.json'),prepared_sha256=sha(output/'prepared.json'),
        code_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},project_ledger_path=str(Path(book).resolve()),
        dependency_versions={n:importlib.metadata.version(n) for n in ('mcp','httpx','jsonschema','pydantic')},external_model_requests=0))
    save(output/'freeze-sha256.json',dict(sha256=sha(output/'freeze.json')))
    return proposal
