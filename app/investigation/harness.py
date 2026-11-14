"""Resumable skill-aware investigation using the existing lease and response journal."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import validate, ValidationError
from app.agent.react_agent import Agent
from app.agent.structured_output import parse_model_json
from app.investigation.contracts import obj, TEXT, ANSWER
from app.investigation.context import messages
from app.investigation.harness_store import HarnessStore
from app.investigation.skills import freeze, read
from app.investigation.runner import verify, ScriptedInvestigator
from app.service.runtime import JournalClient, write_json
from app.service.store import digest, Blocked, Cancelled, LeaseLost
from app.tools.case_tools import strict_json

LOCAL = {
    'search_knowledge':obj({'query':TEXT},['query']),
    'load_skill':obj({'name':TEXT},['name']),
    'read_skill_resource':obj({'name':TEXT,'resource':TEXT},['name','resource']),
    'read_evidence':obj({'evidence_id':TEXT},['evidence_id']),
    'record_plan':obj({k:dict(type='array',maxItems=6,items=TEXT) for k in ('hypotheses','todo','unknowns')},['hypotheses','todo','unknowns']),
}

PROFILES={'full':dict(skills=True,knowledge=True),'no_skill':dict(skills=False,knowledge=True),
          'no_knowledge':dict(skills=True,knowledge=False)}


def available_tool_schemas(schemas,spec,state):
    """Reflect completed activation in each request, without widening permissions.

    Recomputed from the checkpoint so a restored request has the same tool menu.
    A multi-skill task may still activate its remaining frozen skills.
    """
    remaining=sorted(set(spec['skills'])-set(state['skills']))
    result=[]
    for schema in schemas:
        item=deepcopy(schema)
        if item['function']['name']=='load_skill':
            if not remaining:continue
            item['function']['parameters']['properties']['name']['enum']=remaining
        if item['function']['name']=='read_skill_resource':
            resources={name:[p for p in bundle['files'] if p!='SKILL.md'] for name,bundle in spec['skills'].items() if name in state['skills']}
            resources={n:p for n,p in resources.items() if p}
            if not resources:continue
            item['function']['parameters']['properties']['name']['enum']=sorted(resources)
            item['function']['parameters']['properties']['resource']['enum']=sorted({p for paths in resources.values() for p in paths})
        if spec.get('snapshot',{}).get('task_contract')=='fraud-world-v1':
            from app.investigation.fraud_world import remaining_tool_schema
            item=remaining_tool_schema(item,state)
            if item is None:continue
        result.append(item)
    return result


def fingerprint():
    root=Path(__file__).resolve().parents[2]
    paths=list((root/'app/investigation').glob('*.py'))+[root/n for n in ['app/tools/case_tools.py','app/risk/investigation.py','app/risk/case_snapshot.py',
        'app/service/store.py','app/service/runtime.py','app/agent/react_agent.py','app/agent/structured_output.py',
        'app/llm/glm_client.py','app/llm/siliconflow_client.py','app/llm/cost_ledger.py','app/llm/world_ledger.py','app/risk/history.py']]
    return digest({str(p.relative_to(root)):p.read_text(encoding='utf-8') for p in paths})


class InvestigationService:
    def __init__(self,directory,skill_root,*,store=None,profile='full',execution_mode='offline',knowledge_snapshot=None,strategy='adaptive'):
        if profile not in PROFILES or execution_mode not in ('offline','live'):raise ValueError('Invalid host execution profile')
        if strategy not in ('adaptive','all_context','plan_once','all_materials'):raise ValueError('Invalid host strategy')
        self.strategy=strategy
        self.profile,self.execution_mode,self.knowledge_snapshot=profile,execution_mode,deepcopy(knowledge_snapshot)
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.skill_root=Path(skill_root)
        self.store=store or HarnessStore(self.directory/'jobs.sqlite')
        from app.investigation.experience import Experiences
        self.experiences=Experiences(self.store)

    def submit(self,key,snapshot,question):
        if not isinstance(question,str) or not 1<=len(question)<=800: raise ValueError('Bounded question required')
        # Idempotent retries retain the first knowledge release even after publication.
        from app.service.store import Conflict
        with self.store.transaction() as db:
            prior=db.execute('SELECT id,spec FROM jobs WHERE request_key=?',(key,)).fetchone()
            if prior:
                original=json.loads(prior['spec'])
                if original['snapshot']!=snapshot or original['question']['text']!=question or original.get('profile','full')!=self.profile or original['mode']!=self.execution_mode or original.get('strategy','adaptive')!=self.strategy:raise Conflict('idempotency_payload_changed')
                if snapshot.get('task_contract')=='fraud-world-v1':
                    if original.get('world_memory_mode','none')!=getattr(self,'world_memory_mode','none'):raise Conflict('idempotency_memory_condition_changed')
                    override=getattr(self,'world_memory_override',None)
                    if override is not None and digest(override)!=original['world_memory_hash']:raise Conflict('idempotency_memory_payload_changed')
                return prior['id'],False
        from app.investigation.knowledge import corpus,SCOPE,AS_OF
        features=PROFILES[self.profile]
        knowledge=deepcopy(self.knowledge_snapshot) if self.knowledge_snapshot is not None else dict(documents=corpus(),scope=SCOPE,as_of=AS_OF,memory=self.experiences.frozen())
        if snapshot.get('task_contract') in ('raw-dossier-v1','detector-fraud-review-v1','fraud-inquiry-v1','fraud-world-v1'):
            knowledge=dict(documents=[],scope=snapshot['scope'],as_of=snapshot['cutoff'],memory=dict(release=0,release_hash=digest(None),lesson=None))
        if not features['knowledge']:knowledge=dict(documents=[],scope=SCOPE,as_of=AS_OF,memory=dict(release=0,release_hash=digest(None),lesson=None))
        spec=dict(case_id=snapshot['case_id'],snapshot=deepcopy(snapshot),snapshot_hash=digest(snapshot),
                  question={'text':question},mode=self.execution_mode,task_type='investigation',skills=freeze(self.skill_root) if features['skills'] else {},code=fingerprint(),profile=self.profile,
                  knowledge=knowledge,knowledge_hash=digest(knowledge),completion_contract='evidence-v2-plans-advisory',strategy=self.strategy)
        if self.strategy=='all_context' and snapshot.get('task_contract')!='raw-dossier-v1':raise ValueError('All-context strategy requires raw dossiers')
        if self.strategy=='plan_once' and snapshot.get('task_contract')!='fraud-inquiry-v1':raise ValueError('Plan-once strategy requires open fraud inquiry')
        if self.strategy=='all_materials' and snapshot.get('task_contract')!='fraud-world-v1':raise ValueError('All-material strategy requires multi-source fraud cases')
        if snapshot.get('task_contract')=='fraud-world-v1':
            from app.investigation.fraud_world import ANSWER as WORLD_ANSWER
            spec['world_answer_schema']=deepcopy(WORLD_ANSWER)
            spec['completion_contract']='world-originals-checked-interpretations-await-review-v1'
            mode=getattr(self,'world_memory_mode','none');spec['world_memory_mode']=mode
            if mode!='none':
                from app.investigation.world_memory import WorldMemory
                override=getattr(self,'world_memory_override',None)
                spec['world_memory']=deepcopy(override) if override is not None else WorldMemory(self.store).freeze(snapshot)
                spec['world_memory_hash']=digest(spec['world_memory'])
        if snapshot.get('task_contract')=='fraud-inquiry-v1':spec['completion_contract']='inquiry-numeric-facts-semantics-await-review-v1'
        if snapshot.get('task_contract')=='raw-dossier-v1':spec['completion_contract']='citations-checked-semantics-await-review-v1'
        if snapshot.get('task_contract')=='raw-dossier-v1' and 'purchase_cents' in snapshot:
            spec['dossier_review_contract']='support-v3'
        if snapshot.get('task_contract')=='detector-fraud-review-v1':
            from app.investigation.fraud_memory import FraudMemory, EMPTY
            spec['completion_contract']='fraud-leads-not-adjudication-v1'
            spec['fraud_memory']=FraudMemory(self.store).freeze(snapshot) if features['knowledge'] else deepcopy(EMPTY)
            spec['fraud_memory_hash']=digest(spec['fraud_memory'])
        return self.store.submit(key,spec)


class SkillScriptedClient:
    """Offline control consumes the actual compacted state, not benchmark gold."""
    simulation=True

    def chat(self,messages,tools=None):
        frame=json.loads(messages[1]['content'])
        observations=frame['observations']
        available={t['function']['name'] for t in (tools or [])}
        if not any(o.get('kind')=='provisional_plan' for o in observations):
            text=frame['goal'].lower()
            name='evidence-conflict' if 'conflict' in text or '冲突' in text else 'merchant-change' if 'merchant' in text or '商户' in text else 'alert-investigation'
            calls=[('load_skill',dict(name=name)),('record_plan',dict(hypotheses=['Explanation remains unverified'],todo=['Read applicable procedure and case facts'],unknowns=['Evidence coverage unknown'])),
                   ('search_knowledge',dict(query='历史覆盖 清算 商户解释 调查顺序'))]
            calls=[(n,a) for n,a in calls if n in available]
            return dict(role='assistant',content=None,tool_calls=[dict(id='setup-'+str(i),type='function',function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)])
        fact_messages=[dict(role='tool',content=json.dumps(o)) for o in observations if o.get('ok') and 'source' in o]
        response=ScriptedInvestigator().chat(fact_messages,tools)
        if not response.get('tool_calls') and frame['todo']:
            answer=json.loads(response['content'])
            return dict(role='assistant',content=None,tool_calls=[dict(id='close-plan',type='function',function=dict(name='record_plan',arguments=json.dumps(dict(hypotheses=frame['hypotheses'],todo=[],unknowns=answer['missing']))))])
        return response


class HarnessWorker:
    def __init__(self,service,*,client_factory=None,lease_seconds=90,deadline_seconds=180,hook=None,connection_factory=None):
        if client_factory is None and service.execution_mode!='offline':raise ValueError('Explicit live client factory required')
        self.service,self.store=service,service.store
        self.client_factory,self.lease_seconds,self.hook=client_factory or SkillScriptedClient,lease_seconds,hook or (lambda *args:None)
        self.connection_factory=connection_factory
        if type(deadline_seconds) is not int or not 1<=deadline_seconds<=480:raise ValueError('Bounded task deadline required')
        self.deadline_seconds=deadline_seconds
        self.owner=uuid4().hex

    def step(self):
        claim=self.store.claim(self.owner,lease_seconds=self.lease_seconds,deadline_seconds=self.deadline_seconds,mode=self.service.execution_mode)
        if claim is None:return None
        spec=json.loads(claim['spec'])
        def checkpoint(): self.store.checkpoint(claim['id'],self.owner,claim['generation'],lease_seconds=self.lease_seconds)
        try:
            if digest(spec)!=claim['spec_hash'] or spec['code']!=fingerprint() or spec['snapshot_hash']!=digest(spec['snapshot']) or spec.get('knowledge_hash')!=digest(spec.get('knowledge')): raise Blocked('frozen_input_or_code_changed')
            if 'fraud_memory' in spec and digest(spec['fraud_memory'])!=spec.get('fraud_memory_hash'):raise Blocked('frozen_memory_changed')
            if 'world_memory' in spec and digest(spec['world_memory'])!=spec.get('world_memory_hash'):raise Blocked('frozen_method_memory_changed')
            state=self.store.load(claim['id']) or dict(phase='model',turn=0,attempts=0,observations=[],evidence={},trace=[],skills={},expanded=[],
                plan=dict(hypotheses=[],todo=[],unknowns=[]),pending=[],cursor=0,repeats={},repair=False,context_metrics=[])
            checkpoint()
            self.store.save(claim,state)
            asyncio.run(self.execute(claim,spec,state,checkpoint))
        except LeaseLost: pass  # A fenced predecessor must not publish a result.
        except Cancelled:
            self.store.finish(claim['id'],self.owner,claim['generation'],'cancelled')
        except Blocked as exc:
            self.store.finish(claim['id'],self.owner,claim['generation'],'blocked',reason=str(exc))
        except Exception as exc:
            # AnyIO transport task groups can wrap a host stop condition during cleanup.
            while isinstance(exc,BaseExceptionGroup) and len(exc.exceptions)==1:exc=exc.exceptions[0]
            if isinstance(exc,LeaseLost):pass
            elif isinstance(exc,Cancelled):self.store.finish(claim['id'],self.owner,claim['generation'],'cancelled')
            else:self.store.finish(claim['id'],self.owner,claim['generation'],'blocked' if isinstance(exc,Blocked) else 'failed',
                                   reason=str(exc) if isinstance(exc,Blocked) else type(exc).__name__)
        return self.store.get(claim['id'])

    async def execute(self,claim,spec,state,checkpoint):
        from app.investigation.gateway import connect,Gateway
        directory=self.service.directory/claim['id'];directory.mkdir(exist_ok=True)
        snapshot=directory/'snapshot.json';write_json(snapshot,spec['snapshot'])
        fraud=spec['snapshot'].get('task_contract')=='detector-fraud-review-v1'
        inquiry=spec['snapshot'].get('task_contract')=='fraud-inquiry-v1'
        world=spec['snapshot'].get('task_contract')=='fraud-world-v1'
        if (inquiry or world) and self.client_factory is SkillScriptedClient:raise ValueError('Open investigation requires an explicit client; fixed simulation is not applicable')
        if fraud and self.client_factory is SkillScriptedClient:
            from app.investigation.fraud import FraudScriptedClient
            base=FraudScriptedClient()
        else:base=self.client_factory()
        dossier=spec['snapshot'].get('task_contract')=='raw-dossier-v1'
        make_messages, answer_schema, check_answer = messages, ANSWER, verify
        if dossier:
            from app.investigation.dossier import messages as make_messages, ANSWER as answer_schema, verify as raw_verify, DraftReviewError
            check_answer=lambda answer,evidence:raw_verify(answer,evidence,purchase_cents=spec['snapshot'].get('purchase_cents'),support_contract=spec.get('dossier_review_contract') in ('support-v2','support-v3'))
        if fraud:
            from app.investigation.fraud import messages as make_messages, ANSWER as answer_schema, verify as check_answer
        if inquiry:
            from app.investigation.inquiry import messages as make_messages, ANSWER as answer_schema, verify as check_answer
        if world:
            from app.investigation.fraud_world import messages as make_messages,ANSWER as answer_schema,verify as world_verify,DraftError as WorldDraftError,PLAN
            check_answer=lambda a,e:world_verify(a,e,memory_ids={r['memory_id'] for o in state['observations'] if o.get('kind')=='method_memory' for r in o['records']})
        features=PROFILES[spec.get('profile','full')]
        allowed={n:s for n,s in LOCAL.items() if (features['skills'] or n not in ('load_skill','read_skill_resource')) and (features['knowledge'] or n!='search_knowledge')}
        if dossier or fraud:allowed={n:s for n,s in allowed.items() if n not in ('search_knowledge','read_evidence','record_plan')}
        if inquiry:allowed={n:s for n,s in allowed.items() if n in ('load_skill','read_skill_resource','record_plan')}
        if world:
            allowed={n:s for n,s in allowed.items() if n in ('load_skill','read_skill_resource')}
            allowed['update_hypotheses']=PLAN
            if 'world_memory' in spec:allowed['recall_world_methods']=obj()
        if fraud and features['knowledge'] and 'fraud_memory' in spec:allowed['recall_review_memory']=obj({},[])
        client=JournalClient(self.store,claim,base,checkpoint,after_saved=lambda ordinal:self.hook('response_saved',ordinal))
        client.calls=state['turn']
        def save():
            checkpoint();self.store.save(claim,state)
        try:
            if spec['mode']=='offline' and not getattr(base,'simulation',False):raise ValueError('Live client requires host live mode')
            with (directory/'mcp-stderr.log').open('a',encoding='utf-8') as errlog:
                async with (self.connection_factory or connect)(snapshot,spec['snapshot'],errlog=errlog) as (sessions,handshakes):
                    write_json(directory/'mcp-handshakes.json',handshakes)
                    gateway=Gateway(sessions,spec['snapshot'],max_attempts=20)
                    gateway.evidence=deepcopy(state['evidence']);gateway.trace=deepcopy(state['trace'])
                    schemas=gateway.schemas+[dict(type='function',function=dict(name=n,description=n.replace('_',' ')+'. Host-scoped operation; never grants financial permissions.',parameters=s)) for n,s in allowed.items()]
                    if inquiry and not state.get('inquiry_initialized'):
                        calls=[('load_skill',dict(name=n)) for n in sorted(spec['skills'])]
                        calls += [('inquiry_overview',{}),('inquiry_rules',{})]
                        state['pending']=[dict(id='inquiry-init-'+str(i),type='function',function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)]
                        state.update(cursor=0,phase='tools',inquiry_initialized=True);save()
                    if world and not state.get('world_initialized'):
                        calls=[('load_skill',dict(name=n)) for n in sorted(spec['skills'])]+[('inquiry_overview',{})]
                        if 'world_memory' in spec:calls.append(('recall_world_methods',{}))
                        if spec['strategy']=='all_materials':calls.append(('world_materials',{}))
                        state['pending']=[dict(id='world-init-'+str(i),type='function',function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)]
                        state.update(cursor=0,phase='tools',world_initialized=True);save()
                    if dossier and not features['knowledge']:schemas=[s for s in schemas if s['function']['name']!='policy_search']
                    if dossier and spec.get('strategy')=='all_context':
                        # Strong simple baseline: same skill, originals, contract, budget and
                        # verifier; host deterministically loads ALL eligible records first.
                        # No model answer or evaluation annotations participate in collection.
                        if not state.get('all_context_preloaded'):
                            from app.investigation.dossier import selected
                            calls=[('load_skill',dict(name=n)) for n in sorted(spec['skills'])]
                            calls += [('case_read',dict(document_id=d['id'])) for d in sorted(selected(spec['snapshot']).values(),key=lambda d:d['id']) if d['kind']!='policy']
                            calls += [('policy_search',dict(query='disputed payment clearing refund'))]
                            if len(calls)>12:raise Blocked('all_context_tool_budget')
                            state['pending']=[dict(id='host-preload-'+str(i),type='function',function=dict(name=n,arguments=json.dumps(a,sort_keys=True))) for i,(n,a) in enumerate(calls)]
                            state.update(cursor=0,phase='tools',all_context_preloaded=True);save()
                        schemas=[]
                    while state['phase']!='done':
                        checkpoint()
                        if state['phase']=='model':
                            if state['turn']>=6: raise Blocked('request_limit')
                            if dossier and state['turn']==5:state['force_final']=True
                            if inquiry and (state['turn']==5 or spec['strategy']=='plan_once' and state['turn']>=1):state['force_final']=True
                            if world and (state['turn']==5 or spec['strategy']=='all_materials'):state['force_final']=True
                            context,metric=make_messages(spec,state)
                            if state['repair']:
                                context.append(dict(role='user',content='Format correction only, preserve conclusion, no tools. Previous raw answer: '+state['raw']['content']))
                                if len(json.dumps(context).encode())>(180000 if world else 24000): raise Blocked('context_budget')
                            final_only=state['repair'] or state.get('force_final',False) or bool(state.get('draft_feedback'))
                            response=await asyncio.to_thread(client.chat,context,tools=[] if final_only else available_tool_schemas(schemas,spec,state))
                            state['raw']=response;state['turn']+=1;state['context_metrics'].append(metric)
                            # Journal already contains raw even if validation below fails.
                            Agent._validate_assistant_message(response)
                            state['pending']=response.get('tool_calls') or [];state['cursor']=0
                            if dossier and spec.get('strategy')=='all_context' and state['pending']:raise ValueError('All-context model cannot invoke tools')
                            if dossier and final_only and state['pending']:raise ValueError('Final review cannot invoke more tools')
                            if inquiry and final_only and state['pending']:raise ValueError('Inquiry final answer cannot invoke more tools')
                            if world and final_only and state['pending']:raise ValueError('World final answer cannot invoke more tools')
                            if state['repair'] and state['pending']: raise ValueError('Repair cannot call tools')
                            if len(state['pending'])>12: raise Blocked('action_batch_limit')
                            if world:
                                state['world_last_tool_round']=(dict(assistant=deepcopy(state['raw']),results=[]) if state['pending'] else None)
                            state['phase']='tools' if state['pending'] else 'finalize';save()
                        if state['phase']=='tools':
                            while state['cursor']<len(state['pending']):
                                call=state['pending'][state['cursor']]['function'];name=call['name'];arguments=call['arguments']
                                if state['attempts']>=12:
                                    if not (dossier or world):raise Blocked('tool_budget')
                                    # Abort unstarted queued actions. They are never dispatched,
                                    # and no fictitious observations/evidence are generated.
                                    state['observations'].append(dict(ok=False,kind='host_budget_notice',
                                        error='tool_budget_exhausted',unstarted_actions=len(state['pending'])-state['cursor']))
                                    state['cursor']=len(state['pending']);state['force_final']=True;save();break
                                identity=digest([name,arguments]);state['repeats'][identity]=state['repeats'].get(identity,0)+1
                                state['attempts']+=1;save()  # Read intent persists before execution; retries count again.
                                if state['repeats'][identity]>2: raise Blocked('no_progress_repeated_call')
                                try:
                                    if name in LOCAL or name in ('recall_review_memory','update_hypotheses','recall_world_methods'):
                                        if name not in allowed:raise ValueError('Disabled host operation')
                                        args=strict_json(arguments);validate(args,allowed[name])
                                        if name=='load_skill':
                                            if args['name'] in state['skills']:raise ValueError('Skill already active')
                                            data=read(spec['skills'],args['name']);state['skills'][args['name']]=data
                                            observation=dict(ok=True,kind='skill',name=args['name'],version=data['version'])
                                        elif name=='read_skill_resource':
                                            if args['name'] not in state['skills']: raise ValueError('Activate skill first')
                                            data=read(spec['skills'],**args);state['skills'][args['name']+'/'+args['resource']]=data
                                            observation=dict(ok=True,kind='skill_resource',name=args['name'],resource=args['resource'])
                                        elif name=='recall_review_memory':
                                            if features['skills'] and not state['skills']:raise ValueError('Activate a skill first')
                                            from app.investigation.fraud_memory import recall
                                            observation=recall(spec['fraud_memory'])
                                        elif name=='record_plan':
                                            state['plan']=args;observation=dict(ok=True,kind='provisional_plan',**args)
                                        elif name=='update_hypotheses':
                                            from app.investigation.fraud_world import update_plan
                                            observation=update_plan(args,state)
                                        elif name=='recall_world_methods':
                                            from app.investigation.world_memory import recall
                                            observation=recall(spec['world_memory'])
                                            if state.get('world_last_tool_round') is not None:state['world_memory_recalled_by_agent']=True
                                        elif name=='search_knowledge':
                                            if features['skills'] and not state['skills']:raise ValueError('Activate a skill first')
                                            from app.investigation.retrieval import search
                                            from app.investigation.experience import search_memory
                                            knowledge=spec['knowledge']
                                            observation=dict(ok=True,kind='knowledge',
                                                retrieval=search(knowledge['documents'],args['query'],scope=knowledge['scope'],as_of=knowledge['as_of']),
                                                memory=search_memory(knowledge['memory'],args['query']))
                                        else:
                                            key=args['evidence_id']
                                            if key not in state['evidence']:raise ValueError('Unknown evidence')
                                            state['expanded'].append(key);observation=dict(ok=True,kind='expanded_evidence',evidence_id=key)
                                    else:
                                        if features['skills'] and not state['skills']:raise ValueError('Activate a skill first')
                                        if world and spec['strategy']=='adaptive' and name in ('world_read','world_materials','inquiry_query','inquiry_rows') and not state.get('hypothesis_updates'):
                                            raise ValueError('Record initial hypotheses before content reads')
                                        if dossier and name=='policy_search' and not features['knowledge']:raise ValueError('Disabled policy retrieval')
                                        observation=await gateway.call(name,arguments)
                                        state['evidence']=deepcopy(gateway.evidence);state['trace']=deepcopy(gateway.trace)
                                except (ValueError,ValidationError,KeyError) as exc:
                                    observation=dict(ok=False,error='invalid_host_operation')
                                    if world:
                                        detail=(dict(field=list(exc.absolute_path),rule=exc.validator,expected=exc.validator_value) if isinstance(exc,ValidationError)
                                                else dict(message=str(exc)[:200]))
                                        observation.update(operation=name,detail=detail)
                                self.hook('tool_returned',state['attempts'])
                                if world and state.get('world_last_tool_round') is not None:
                                    state['world_last_tool_round']['results'].append(dict(tool_call_id=state['pending'][state['cursor']]['id'],observation_index=len(state['observations'])))
                                state['observations'].append(observation);state['cursor']+=1;save()
                                self.hook('tool_checkpoint',state['attempts'])
                            if dossier and state['attempts']>=12:state['force_final']=True
                            state['phase']='model';save()
                        if state['phase']=='finalize':
                            try:
                                answer,_=parse_model_json(state['raw']['content']);validate(answer,answer_schema)
                            except (ValueError,ValidationError):
                                if state['repair']:raise
                                state['repair']=True;state['phase']='model';save();continue
                            state['proposed_answer']=answer;save()
                            if features['skills'] and not state['skills']:raise ValueError('Skill required')
                            try:
                                if world:
                                    from app.investigation.fraud_world import materialize_timeline
                                    answer,changes=materialize_timeline(answer,state['evidence'])
                                    state.setdefault('host_timeline_materialization',[]).append(dict(turn=state['turn'],changes=changes))
                                validation=check_answer(answer,state['evidence'])
                            except ValueError as exc:
                                if not ((dossier and isinstance(exc,DraftReviewError)) or (world and isinstance(exc,WorldDraftError))):raise
                                state.setdefault('draft_reviews',[]).append(dict(answer=deepcopy(answer),issues=deepcopy(exc.issues)))
                                if len(state['draft_reviews'])>1 or state['turn']>=6:
                                    save();raise
                                state['draft_feedback']=exc.issues;state['repair']=False;state['phase']='model';save();continue
                            if world and spec['strategy']=='adaptive':
                                from app.investigation.fraud_world import commit_final_hypotheses
                                commit_final_hypotheses(answer,state)
                            # Host-owned evidence predicates decide structural completion.
                            # Model-authored planning text stays visible for review; it is
                            # neither silently erased nor treated as a factual predicate.
                            warnings=[]
                            if dossier:warnings.append(dict(kind='semantic_review_required',items=['Original line references were checked; a human must verify interpretation, arithmetic and policy application.']))
                            if state['plan']['todo']:warnings.append(dict(kind='open_plan_items',items=deepcopy(state['plan']['todo'])))
                            if state['plan']['unknowns']:warnings.append(dict(kind='plan_uncertainties',items=deepcopy(state['plan']['unknowns'])))
                            state.update(answer=answer,validation=validation,review_warnings=warnings,phase='done');save()
                    result=dict(status='world_sources_awaiting_review' if world else 'inquiry_facts_awaiting_review' if inquiry else 'citations_checked_awaiting_review' if dossier else 'fraud_leads_awaiting_review' if fraud else 'completed_validated',answer=state['answer'],validation=state['validation'],
                                turns=state['turn'],attempts=state['attempts'],active_skills={k:v['version'] for k,v in state['skills'].items()},
                                mode=spec['mode'],new_client_requests=client.new_calls,replayed_client_responses=client.replayed)
                    result['client_kind']='simulation' if getattr(base,'simulation',False) else 'configured_external_client'
                    result['review_warnings']=state.get('review_warnings',[])
                    result['completion_contract']=spec.get('completion_contract')
                    if dossier:
                        result['draft_reviews']=state.get('draft_reviews',[])
                        result['dossier_review_contract']=spec.get('dossier_review_contract')
                    if world:
                        result['host_timeline_materialization']=deepcopy(state.get('host_timeline_materialization',[]))
                        result['draft_reviews']=state.get('draft_reviews',[])
                        result['hypothesis_updates']=deepcopy(state.get('hypothesis_updates',[]))
                        result['access_cost_units']=sum(t.get('cost_units',0) for t in state['trace'])
                        if 'world_memory' in spec:
                            result['method_memory_snapshot']=dict(hash=spec['world_memory_hash'],eligible=len(spec['world_memory']['records']),
                                  rejected=spec['world_memory'].get('rejected',[]),recalled=bool(state.get('world_memory_recalled_by_agent')),
                                  context_supplied=any(o.get('kind')=='method_memory' for o in state['observations']))
                    result['knowledge_snapshot']=dict(hash=spec['knowledge_hash'],release=spec['knowledge']['memory']['release'])
                    if fraud and 'fraud_memory' in spec:
                        result['review_memory_snapshot']=dict(hash=spec['fraud_memory_hash'],release=spec['fraud_memory']['release'],
                            eligible=len(spec['fraud_memory']['records']),recalled=any(o.get('kind')=='review_memory' for o in state['observations']))
                    write_json(directory/'report.json',result)
                    self.store.finish(claim['id'],self.owner,claim['generation'],'awaiting_review',result=result)
        finally:
            if hasattr(base,'close'):base.close()
