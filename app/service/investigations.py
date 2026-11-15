"""Optional stage-two routes on the existing authenticated workbench."""
import importlib.util
from threading import RLock
from fastapi.responses import HTMLResponse
from fastapi import Request
from pydantic import BaseModel,ConfigDict,Field
_SERVICE_LOCK=RLock()


def get_service(parent):
    if importlib.util.find_spec('mcp') is None or importlib.util.find_spec('jsonschema') is None:
        raise ValueError('Install requirements-investigation.txt')
    with _SERVICE_LOCK:
        if not hasattr(parent,'investigations'):
            from app.investigation.harness import InvestigationService
            parent.investigations=InvestigationService(parent.settings.directory/'investigations',parent.settings.repo/'investigation_skills')
    return parent.investigations


def step(parent):
    if not (parent.settings.directory/'investigations/jobs.sqlite').exists():return None
    from app.investigation.harness import HarnessWorker
    class OfflineWorkbenchWorker(HarnessWorker):
        async def execute(self,claim,spec,state,checkpoint):
            if spec['snapshot'].get('task_contract')=='fraud-world-v1':
                if spec['mode']!='offline' or spec['strategy']!='all_materials':raise ValueError('Explicit code-control task required')
                from app.investigation.world_baseline import BaselineProbeClient
                self.client_factory=BaselineProbeClient
            return await super().execute(claim,spec,state,checkpoint)
    return OfflineWorkbenchWorker(get_service(parent)).step()


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

    @app.get('/world-cases')
    def world_cases():
        from app.service.world_catalog import read_entry
        from app.investigation.fraud_world import World
        rows=[]
        for identity,entry in getattr(parent,'world_catalog',{}).items():
            snapshot=read_entry(entry);world=World(snapshot)
            rows.append(dict(case_id=identity,cutoff=snapshot['cutoff'],investigation_as_of=snapshot['investigation_as_of'],
                             subject_id=world.a.target['user_id'],alert=snapshot['transaction_snapshot']['alert'],
                             available_originals=len(world.eligible),origin=snapshot['source_origin']))
        return dict(cases=rows,origin='IBM合成交易 + 独立模拟调查原件',creation_mode='offline_code_control_only')

    @app.post('/world-investigations')
    def submit_world(body:FraudBody,request:Request):
        from app.service.world_catalog import read_entry
        from app.investigation.world_service import WorldService
        key=request.headers.get('idempotency-key','')
        if not 8<=len(key)<=128 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in key):raise ValueError('Idempotency key required')
        registered=getattr(parent,'world_catalog',{})
        if body.case_id not in registered:raise ValueError('Registered A/B case required')
        s=get_service(parent);control=WorldService(s.directory,store=s.store,strategy='all_materials')
        identity,_=control.submit(key,read_entry(registered[body.case_id]),body.text)
        return dict(id=identity,mode='offline_code_control_NOT_LLM')

    @app.get('/world-methods')
    def world_methods():
        from app.investigation.world_memory import WorldMemory
        memory=WorldMemory(get_service(parent).store);head=memory.head();previews=[]
        for record in head['records']:
            item=memory.get(record['candidate']);body=item['body']
            if body.get('kind')!='reviewed_procedural_method':continue
            previews.append(dict(candidate=item['id'],topic=body['focus'],lesson=body['lesson'],expires=item['expires'],
                                 feedback_origin=body['feedback_origin'],source_client_kind=body['source_client_kind']))
        return dict(release=head['release'],head=head['head'],records=previews,authority='advisory_methods_only')

    @app.post('/world-methods/{identity}/withdraw')
    def withdraw_world_method(identity:str,body:WithdrawMemoryBody):
        from app.investigation.world_memory import WorldMemory
        memory=WorldMemory(get_service(parent).store)
        if memory.get(identity)['body'].get('kind')!='reviewed_procedural_method':raise ValueError('World method required')
        return memory.withdraw(identity,body.expected_head)

    @app.get('/investigations')
    def jobs():return {'jobs':get_service(parent).store.list_jobs()}

    @app.get('/fraud-memory')
    def memory_head():
        memory=fraud_memory();head=memory.head()
        previews=[dict(candidate=r['candidate'],case_id=b['case_id'],focus=b['focus']) for r in head['records'] if (b:=memory.get(r['candidate'])['body']).get('kind')!='reviewed_procedural_method']
        return dict(**head,previews=previews)

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
                    question=row['spec']['question']['text'],task_contract=row['spec']['snapshot'].get('task_contract'),strategy=row['spec'].get('strategy'),
                    checkpoint=s.store.load(identity),events=s.store.events(identity),ticket=tickets().get(identity),
                    review_memory_snapshot=row['spec'].get('fraud_memory'),
                    method_memory_snapshot=row['spec'].get('world_memory'),memory_condition=row['spec'].get('world_memory_mode'),
                    investigation_scope={k:row['spec']['snapshot'][k] for k in ('cutoff','investigation_as_of','access_budget_units','source_origin')} if row['spec']['snapshot'].get('task_contract')=='fraud-world-v1' else None,
                    knowledge_snapshot=dict(hash=row['spec']['knowledge_hash'],release=row['spec']['knowledge']['memory']['release']))

    @app.post('/investigations/{identity}/cancel')
    def cancel(identity:str):return {'state':get_service(parent).store.cancel(identity)}

    @app.post('/investigations/{identity}/draft')
    def draft(identity:str,body:DraftBody):return tickets().draft(identity,body.text,expected_hash=body.expected_hash)

    @app.post('/investigations/{identity}/approve')
    def approve(identity:str,body:ApprovalBody):return tickets().approve(identity,body.expected_hash,reviewed_claims=body.reviewed_claims)

    @app.post('/investigations/{identity}/submit-ticket')
    def submit_ticket(identity:str,body:ApprovalBody):return tickets().submit(identity,body.expected_hash)
