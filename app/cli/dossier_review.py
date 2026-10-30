"""Prepare a new synthetic dossier, explicitly execute it, then review the saved result.

This product entry accepts no gold annotations and never imports the evaluator.
Preparing/status are offline. Running requires both --live and an explicit cost cap.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4

from app.investigation.dossier_service import DossierService
from app.investigation.harness import HarnessWorker
from app.investigation.recording import RecordedClient
from app.service.runtime import write_json
from app.service.store import digest


def prepare(input_path,directory,*,strategy='all_context'):
    if directory.exists():raise ValueError('Use a new execution directory')
    if input_path.stat().st_size>120000:raise ValueError('Input size limit')
    packet=json.loads(input_path.read_text(encoding='utf-8'))
    if not isinstance(packet,dict) or set(packet)!={'snapshot','question'}:raise ValueError('Only snapshot and question are accepted; no gold')
    if packet['snapshot'].get('data_kind')!='synthetic_development':raise ValueError('This release accepts explicitly synthetic dossiers only')
    if 'gold' in packet['snapshot']:raise ValueError('Evaluation annotations are not product input')
    service=DossierService(directory/'harness',strategy=strategy,execution_mode='live')
    job,_=service.submit('product-'+digest(packet),packet['snapshot'],packet['question'])
    manifest=dict(job=job,batch_id=uuid4().hex,strategy=strategy,input_hash=digest(packet),
                  state='prepared_no_model_requests',provider='SiliconFlow China',
                  destination='https://api.siliconflow.cn/v1/chat/completions')
    write_json(directory/'product.json',manifest)
    return manifest


def execute(directory,*,live,max_estimated_cny,client_factory=None,connection_factory=None):
    if not live:raise ValueError('Explicit --live is required; preparation never sends documents')
    amount=Decimal(str(max_estimated_cny))
    if not amount.is_finite() or not Decimal('0')<amount<=Decimal('1'):raise ValueError('Explicit estimate ceiling must be >0 and <=1 CNY')
    manifest=json.loads((directory/'product.json').read_text(encoding='utf-8'))
    service=DossierService(directory/'harness',strategy=manifest['strategy'],execution_mode='live')
    row=service.store.get(manifest['job'])
    packet=dict(snapshot=row['spec']['snapshot'],question=row['spec']['question']['text'])
    if digest(packet)!=manifest['input_hash'] or row['spec']['strategy']!=manifest['strategy'] or row['spec']['mode']!='live':
        raise ValueError('Prepared input or strategy changed')
    if row['state']!='queued':
        return dict(state=row['state'],job=row['id'],new_model_requests=0,reason='Existing run is not automatically retried')
    # Bind the transport policy before obtaining a credential or constructing HTTP.
    root=Path(__file__).resolve().parents[2]
    policy=json.loads((root/'config/siliconflow-live-v1.json').read_text(encoding='utf-8'))
    policy['run_budget_cny']=str(amount)
    authorization=dict(batch_id=manifest['batch_id'],job=manifest['job'],input_hash=manifest['input_hash'],policy=policy)
    saved=directory/'execution-policy.json'
    if saved.exists() and json.loads(saved.read_text(encoding='utf-8'))!=authorization:raise ValueError('Execution policy changed')
    if not saved.exists():write_json(saved,authorization)
    client=None
    def factory():
        nonlocal client
        if client_factory is None:
            from app.cli.investigate_live import local_key
            from app.investigation.budget import BatchLedger
            from app.llm.siliconflow_client import SiliconFlowClient
            key=local_key()
            if not key:raise ValueError('Saved local credential missing')
            base=SiliconFlowClient(key,policy=policy,ledger=BatchLedger(root/'runs/llm-budget.sqlite',manifest['batch_id']),
                                  run_id='multisource-'+manifest['batch_id']+':product:'+manifest['job'])
        else:base=client_factory()
        client=RecordedClient(base,directory)
        return client
    row=HarnessWorker(service,client_factory=factory,connection_factory=connection_factory).step()
    state=service.store.load(manifest['job']) or {}
    output=dict(job=manifest['job'],state=row['state'] if row else service.store.get(manifest['job'])['state'],
                reason=row.get('reason') if row else None,answer=state.get('answer') or state.get('proposed_answer'),
                validation=state.get('validation'),evidence=state.get('evidence',{}),draft_reviews=state.get('draft_reviews',[]),
                evaluation_score=None,meaning='Source checks and human review; no hidden gold evaluation in this product path')
    write_json(directory/'result.json',output)
    if client:write_json(directory/'usage.json',client.snapshot())
    return dict(state=output['state'],job=manifest['job'],result=str(directory/'result.json'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','run','status'])
    p.add_argument('--directory',type=Path,required=True);p.add_argument('--input',type=Path)
    p.add_argument('--strategy',choices=['all_context','adaptive'],default='all_context')
    p.add_argument('--live',action='store_true');p.add_argument('--max-estimated-cny')
    a=p.parse_args()
    if a.action=='prepare':
        if a.input is None:p.error('--input required')
        result=prepare(a.input,a.directory,strategy=a.strategy)
    elif a.action=='run':
        if not a.live or a.max_estimated_cny is None:p.error('run requires --live and --max-estimated-cny; this sends the frozen synthetic dossier to SiliconFlow')
        result=execute(a.directory,live=a.live,max_estimated_cny=a.max_estimated_cny)
    else:
        m=json.loads((a.directory/'product.json').read_text(encoding='utf-8'))
        row=DossierService(a.directory/'harness',strategy=m['strategy'],execution_mode='live').store.get(m['job'])
        result=dict(job=m['job'],state=row['state'],reason=row['reason'])
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
