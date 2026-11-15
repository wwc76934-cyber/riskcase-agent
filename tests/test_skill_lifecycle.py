"""Lifecycle contract regression; simulated clients are not provider reruns."""
import json
from copy import deepcopy
from app.investigation.fraud import FraudScriptedClient
from app.investigation.fraud_service import FraudService
from app.investigation.harness import HarnessWorker
from tests.test_fraud_bridge import fraud_case,manifest


class AvailabilityProbe:
    simulation=True
    def __init__(self):self.menus=[]
    def chat(self,messages,tools=None):
        names=[t['function']['name'] for t in tools or []];self.menus.append(names)
        observations=json.loads(messages[1]['content'])['observations']
        if 'load_skill' in names:
            calls=[('load_skill',{'name':'fraud-alert-review'})]
        elif not any(o.get('evidence_id') for o in observations):
            calls=[('fraud_alert',{}),('fraud_policy',{}),('fraud_history',{'lookback_hours':1}),
                   ('fraud_history',{'lookback_hours':720}),('fraud_merchant',{'lookback_hours':720})]
        else:return FraudScriptedClient().chat(messages,tools)
        return dict(role='assistant',content=None,tool_calls=[dict(id='probe-'+str(i),type='function',
                    function=dict(name=name,arguments=json.dumps(args))) for i,(name,args) in enumerate(calls)])


def test_completed_skill_activation_is_removed_from_next_tool_menu(tmp_path,fraud_case):
    _,snapshot=fraud_case;service=FraudService(tmp_path/'service',profile='no_knowledge')
    job,_=service.submit('lifecycle-probe',snapshot,'Review the bound alert')
    probe=AvailabilityProbe();row=HarnessWorker(service,client_factory=lambda:probe).step()
    assert row['state']=='awaiting_review',row['reason']
    assert 'load_skill' in probe.menus[0]
    assert all('load_skill' not in names for names in probe.menus[1:])
    state=service.store.load(job)
    assert len(state['evidence'])==5 and state['attempts']==6


def test_menu_keeps_only_unloaded_skills_and_is_checkpoint_stable():
    from app.investigation.harness import available_tool_schemas,LOCAL
    schemas=[dict(type='function',function=dict(name='load_skill',parameters=deepcopy(LOCAL['load_skill']))),
             dict(type='function',function=dict(name='fraud_alert',parameters={'type':'object'}))]
    original=deepcopy(schemas);spec={'skills':{'one':{},'two':{}}};state={'skills':{'one':{}}}
    menu=available_tool_schemas(schemas,spec,state)
    assert menu[0]['function']['parameters']['properties']['name']['enum']==['two']
    assert available_tool_schemas(schemas,spec,json.loads(json.dumps(state,sort_keys=True)))==menu
    assert schemas==original and available_tool_schemas([],spec,state)==[]
    state['skills']['two']={}
    assert [s['function']['name'] for s in available_tool_schemas(schemas,spec,state)]==['fraud_alert']


def test_fraud_prompt_does_not_request_disabled_or_completed_activation():
    from app.investigation.fraud import messages
    spec={'skills':{},'question':{'text':'review'}}
    state={'skills':{},'observations':[],'turn':0,'attempts':0}
    context,_=messages(spec,state)
    assert 'Activate fraud-alert-review before' not in context[0]['content']
    assert json.loads(context[1]['content'])['active_skills']==[]
    # A loaded instruction is explicit current state, not another activation request.
    state['skills']={'fraud-alert-review':{'text':'Keep the source cutoff'}}
    context,_=messages(spec,state)
    assert 'already active' in context[0]['content']
    assert json.loads(context[1]['content'])['active_skills']==['fraud-alert-review']


def test_model_cannot_force_reactivation_by_ignoring_tool_menu(tmp_path,fraud_case):
    class IgnoresMenu:
        simulation=True
        def chat(self,messages,tools=None):
            return dict(role='assistant',content=None,tool_calls=[dict(id='bad',type='function',
                        function=dict(name='load_skill',arguments='{"name":"fraud-alert-review"}'))])
    _,snapshot=fraud_case;service=FraudService(tmp_path/'service',profile='no_knowledge')
    job,_=service.submit('ignored-menu-probe',snapshot,'Review the alert')
    row=HarnessWorker(service,client_factory=IgnoresMenu).step()
    assert row['state']=='blocked' and row['reason']=='no_progress_repeated_call'
    observations=service.store.load(job)['observations']
    assert sum(o.get('kind')=='skill' for o in observations)==1
    assert any(o.get('error')=='invalid_host_operation' for o in observations)


def test_offline_no_skill_control_still_collects_all_current_evidence(tmp_path,fraud_case):
    _,snapshot=fraud_case;service=FraudService(tmp_path/'no-skill',profile='no_skill')
    job,_=service.submit('no-skill-control',snapshot,'Review the bound alert')
    row=HarnessWorker(service).step()
    assert row['state']=='awaiting_review',row['reason']
    state=service.store.load(job)
    assert state['skills']=={} and len(state['evidence'])==5
    assert not any(o.get('error') for o in state['observations'])
