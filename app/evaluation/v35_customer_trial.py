"""Freeze a new observed-development reliability trial without granting authority."""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import importlib.metadata
import shutil
import sqlite3

from app.evaluation.v3_pilot_ledger import V35
from app.evaluation.v3_typed_repair import ROOT, load, save, sha
from app.evaluation.v31_trial import bound
from app.investigation.v3.rule_workflow import collect_rule_workflow
from app.investigation.v3.schema_client import delivery_format
from app.investigation.v3.study_delivery import ReliabilityReviewContract
from app.llm.cost_ledger import CostLedger
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess


def prepare(previous, output):
    previous,output=Path(previous),Path(output)
    freeze=load(previous/'freeze.json')
    proposal=load(previous/'proposal.json')
    prepared=load(previous/'prepared.json')
    if (proposal['schema']!='v34-selected-fact-development-v1'
        or sha(previous/'freeze.json')!=load(previous/'freeze-sha256.json')['sha256']
        or sha(previous/'proposal.json')!=freeze['proposal_sha256']
        or sha(previous/'prepared.json')!=freeze['prepared_sha256']):
        raise ValueError('Verified previous observed-development proposal required')
    if [entry['case_id'] for entry in prepared['cases']] != proposal['case_ids'] or len(proposal['case_ids'])!=6:
        raise ValueError('Same six observed cases required')
    for entry in prepared['cases']:
        for field,folder in [('runtime','runtime'),('reference','controller')]:
            name=folder+'/'+entry['case_id']+'.json'
            if entry[field]!=name or sha(previous/name)!=entry[field+'_sha256']:
                raise ValueError('Observed development artifact changed')
    ledger_path=Path(freeze['project_ledger_path'])
    with closing(sqlite3.connect(ledger_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        prior=CostLedger.committed(db.execute('SELECT reserved_cny,estimated_cny FROM reservations'))+CostLedger.holds(db)
    request_limit=Decimal('0.034304')
    maximum=request_limit*60+Decimal('.50')
    if prior<Decimal(proposal['project_before_pilot_committed_cny']) or prior+maximum>34:
        raise ValueError('Original project ledger or conservative headroom unavailable')

    output.mkdir(parents=True,exist_ok=False)
    for name in ('runtime','controller'):shutil.copytree(previous/name,output/name)
    save(output/'prepared.json',prepared)
    store=JobStore(output/'offline-source-preflight.sqlite')
    bounds=[]
    for entry in prepared['cases']:
        identity=entry['case_id']; snapshot=load(output/entry['runtime'])
        if any(record['cost_units'] for record in snapshot['records']):
            raise ValueError('Reliability run may not require model-selected query planning')
        store.submit(identity,dict(mode='offline',case_id=identity,question=dict(text='Development protocol preflight')))
        claim=store.claim('v35-preflight',lease_seconds=120,deadline_seconds=600,mode='offline')
        source=SourceAccess(store.path,claim,snapshot)
        for stage in range(3):
            if stage:source.advance(stage)
            evidence=collect_rule_workflow(source)
            contract=ReliabilityReviewContract(evidence.target_id)
            if stage==0 and contract.initial_extraction(evidence) is None:
                raise ValueError('All frozen T0 packets must fit the single-call phase')
            bounds.append(bound(evidence,report_contract_factory=ReliabilityReviewContract,response_format_factory=delivery_format))
    if max(bounds)>24000:
        raise ValueError('Reliability preflight exceeds frozen wire bound')
    proposal.update(schema=V35,delivery_contract=ReliabilityReviewContract.name,
        transport_contract='v35-known-response-schema-v1',
        max_requests=60,max_requests_per_case_run=5,max_estimated_cny='2.60',
        project_before_pilot_committed_cny=str(prior),known_usage_request_upper_cny=str(request_limit*60),
        known_usage_plus_one_full_unknown_case_upper_cny=str(maximum),
        largest_preflight_context_bound_bytes=max(bounds),
        purpose='Observed six-case customer-semantics reliability gate; no formal Agent or memory comparison',
        financial_scope='New explicit 2.60 CNY batch grant; existing 34 CNY project limit; stop unknown or mathematically unreachable',
        parent_proposal_sha256=freeze['proposal_sha256'])
    proposal['price'].update(checked_on='2026-11-09',
        source='https://www.siliconflow.cn/news/ok5ja7brf921kgf1644yyedz')
    save(output/'proposal.json',proposal)
    files=sorted((ROOT/'app').rglob('*.py'))+[ROOT/'config/siliconflow-world-v2.json']
    for path in files:
        destination=output/'source'/path.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,destination)
    save(output/'freeze.json',dict(schema='v35-customer-development-freeze-v1',
        proposal_sha256=sha(output/'proposal.json'),prepared_sha256=sha(output/'prepared.json'),
        code_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},project_ledger_path=str(ledger_path.resolve()),
        dependency_versions={n:importlib.metadata.version(n) for n in ('mcp','httpx','jsonschema','pydantic')},
        external_model_requests=0))
    save(output/'freeze-sha256.json',dict(sha256=sha(output/'freeze.json')))
    return proposal
