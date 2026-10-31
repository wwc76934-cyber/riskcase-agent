"""Optional stage-two routes on the existing authenticated workbench."""
import importlib.util
from fastapi.responses import HTMLResponse
from fastapi import Request
from pydantic import BaseModel,ConfigDict,Field


def get_service(parent):
    if importlib.util.find_spec('mcp') is None or importlib.util.find_spec('jsonschema') is None:
        raise ValueError('Install requirements-investigation.txt')
    if not hasattr(parent,'investigations'):
        from app.investigation.harness import InvestigationService
        parent.investigations=InvestigationService(parent.settings.directory/'investigations',parent.settings.repo/'investigation_skills')
    return parent.investigations


def step(parent):
    if not (parent.settings.directory/'investigations/jobs.sqlite').exists():return None
    from app.investigation.harness import HarnessWorker
    return HarnessWorker(get_service(parent)).step()


class RequestBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    case_id:str=Field(pattern=r'^case-(0[1-9]|1[0-2])$')
    text:str=Field(min_length=1,max_length=800)


class FraudBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    case_id:str=Field(min_length=1,max_length=120)
    text:str=Field(min_length=1,max_length=800)


class DraftBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    text:str=Field(min_length=1,max_length=2000)
    expected_hash:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')


class ApprovalBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    reviewed_claims:list[str]|None=Field(default=None,max_length=6)


class MethodBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    source_job:str=Field(min_length=1,max_length=64)
    order:list[str]=Field(min_length=3,max_length=3)
    parent:str|None=Field(default=None,max_length=64)


class ReleaseBody(ApprovalBody):
    expected_head:str=Field(pattern=r'^[0-9a-f]{64}$')


class RollbackBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    target:int=Field(ge=0)
    expected_head:str=Field(pattern=r'^[0-9a-f]{64}$')


class FraudMemoryBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    source_job:str=Field(min_length=1,max_length=64)
    expected_report_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    focus:str=Field(pattern=r'^(none|authorization|coverage|merchant_context)$')


class WithdrawMemoryBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_head:str=Field(pattern=r'^[0-9a-f]{64}$')


def install(app,parent):
    def fraud_memory():
        from app.investigation.fraud_memory import FraudMemory
        return FraudMemory(get_service(parent).store)

    def tickets():
        from app.investigation.tickets import Tickets,MockDestination
        s=get_service(parent)
        return Tickets(s.store,MockDestination(s.directory/'mock-destination.sqlite'))

    @app.get('/investigations/page',response_class=HTMLResponse)
    def page():
        from app.service.investigation_page import HTML
        return HTML

    @app.get('/fraud-alerts')
    def fraud_alerts():
        from app.investigation.fraud import from_case
        from app.risk.review_groups import group_id
        rows=[];groups={}
        for identity,path in parent.settings.catalog.items():
            try:
                snapshot=from_case(path,identity)
                m=snapshot['manifest'];day=snapshot['cutoff'][:10]
                key=group_id(m['source_facts_sha256'],m['alert']['model_report_sha256'],m['user_id'],day)
                groups.setdefault(key,dict(group_id=key,user_id=m['user_id'],day=day,
                                            source_version=m['source_facts_sha256'][:8],model_version=m['alert']['model_report_sha256'][:8],case_ids=[]))['case_ids'].append(identity)
                rows.append(dict(case_id=identity,alert=snapshot['manifest']['alert'],cutoff=snapshot['cutoff'],
                                 origin=snapshot['source_origin'],group_id=key))
            except (ValueError,KeyError,OSError,TypeError):continue
        rows.sort(key=lambda r:(r['cutoff'][:10],r['alert']['day_rank'],r['case_id']))
        return dict(alerts=rows,groups=sorted(groups.values(),key=lambda g:(g['day'],g['group_id'])),group_scope='loaded_catalog_only_NOT_all_selected_alerts',
                    mode='offline_deterministic_NOT_LLM',source_authentication='local_registered_projection_not_bank_certification')

    @app.post('/fraud-investigations')
    def submit_fraud(body:FraudBody,request:Request):
        from app.investigation.fraud import from_case
        from app.investigation.fraud_service import FraudService
        key=request.headers.get('idempotency-key','')
        if not 8<=len(key)<=128 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in key):raise ValueError('Idempotency key required')
        if body.case_id not in parent.settings.catalog:raise ValueError('Registered detector case required')
        s=get_service(parent)
        service=FraudService(s.directory,store=s.store)
        snapshot=from_case(parent.settings.catalog[body.case_id],body.case_id)
        identity,_=service.submit(key,snapshot,body.text)
        return dict(id=identity,mode='offline_deterministic_NOT_LLM')

    @app.get('/investigations')
    def jobs():return {'jobs':get_service(parent).store.list_jobs()}

    @app.get('/fraud-memory')
    def memory_head():
        memory=fraud_memory();head=memory.head()
        return dict(**head,previews=[dict(candidate=r['candidate'],case_id=(b:=memory.get(r['candidate'])['body'])['case_id'],focus=b['focus']) for r in head['records']])

    @app.post('/fraud-memory/candidates')
    def memory_candidate(body:FraudMemoryBody):
        return fraud_memory().candidate(body.source_job,body.expected_report_hash,focus=body.focus)

    @app.post('/fraud-memory/candidates/{identity}/assess')
    def memory_assess(identity:str,body:ApprovalBody):
        return fraud_memory().assess(identity,body.expected_hash)

    @app.post('/fraud-memory/candidates/{identity}/publish')
    def memory_publish(identity:str,body:ReleaseBody):
        return fraud_memory().publish(identity,body.expected_hash,body.expected_head,reviewed_claims=body.reviewed_claims)

    @app.post('/fraud-memory/candidates/{identity}/withdraw')
    def memory_withdraw(identity:str,body:WithdrawMemoryBody):
        return fraud_memory().withdraw(identity,body.expected_head)

    @app.get('/methods')
    def methods():return get_service(parent).experiences.listing()

    @app.post('/methods/candidates')
    def candidate(body:MethodBody):
        return get_service(parent).experiences.candidate(body.source_job,body.order,parent=body.parent)

    @app.post('/methods/candidates/{identity}/evaluate')
    def assess(identity:str):return get_service(parent).experiences.assess(identity)

    @app.get('/methods/candidates/{identity}')
    def get_candidate(identity:str):return get_service(parent).experiences.get(identity)

    @app.post('/methods/candidates/{identity}/publish')
    def publish(identity:str,body:ReleaseBody):
        return get_service(parent).experiences.publish(identity,body.expected_hash,body.expected_head)

    @app.post('/methods/rollback')
    def rollback(body:RollbackBody):return get_service(parent).experiences.rollback(body.target,body.expected_head)

    @app.post('/investigations')
    def submit(body:RequestBody,request:Request):
        from app.investigation.fixtures import development_cases
        key=request.headers.get('idempotency-key','')
        if not 8<=len(key)<=128 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in key):raise ValueError('Idempotency key required')
        case=next(c for c in development_cases() if c['id']==body.case_id)
        identity,_=get_service(parent).submit(key,case['snapshot'],body.text)
        return {'id':identity,'mode':'offline_scripted'}

    @app.get('/investigations/{identity}')
    def status(identity:str):
        s=get_service(parent);row=s.store.get(identity)
        return dict(id=identity,state=row['state'],reason=row['reason'],generation=row['generation'],result=row['result'],report_hash=row['result_hash'],execution_mode=row['spec']['mode'],
                    checkpoint=s.store.load(identity),events=s.store.events(identity),ticket=tickets().get(identity),
                    review_memory_snapshot=row['spec'].get('fraud_memory'),
                    knowledge_snapshot=dict(hash=row['spec']['knowledge_hash'],release=row['spec']['knowledge']['memory']['release']))

    @app.post('/investigations/{identity}/cancel')
    def cancel(identity:str):return {'state':get_service(parent).store.cancel(identity)}

    @app.post('/investigations/{identity}/draft')
    def draft(identity:str,body:DraftBody):return tickets().draft(identity,body.text,expected_hash=body.expected_hash)

    @app.post('/investigations/{identity}/approve')
    def approve(identity:str,body:ApprovalBody):return tickets().approve(identity,body.expected_hash,reviewed_claims=body.reviewed_claims)

    @app.post('/investigations/{identity}/submit-ticket')
    def submit_ticket(identity:str,body:ApprovalBody):return tickets().submit(identity,body.expected_hash)
