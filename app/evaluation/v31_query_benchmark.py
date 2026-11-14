"""Materialize a separate AI-author simulation evaluation candidate set."""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
from pathlib import Path

from app.evaluation.v3_narratives import EVALUATION_CANDIDATES
from app.evaluation.v3_tasks import case as candidate_case, write_new
from app.evaluation.v31_query_narratives import QUERY_EVALUATION
from app.evaluation.v31_query_score import REFERENCE
from app.investigation.contracts import digest
from app.investigation.v3.query_world import CONTRACT, SourceQueryWorld
from app.investigation.v3.world import KIND


def choice(value, sources=(), *, acquired=()):
    return dict(values=[value] if isinstance(value, str) else list(value), sources=list(sources),
                requires_originals=list(acquired))


def observation(identity, predicate, value):
    return dict(source_id=identity, predicate=predicate,
                values=list(value) if isinstance(value, (list,tuple)) else [value])


def stage_reference(world, expected, conditional, facts, needed, evidence, *, counters=(), profile='normal'):
    gaps = ['confirmed_case_outcome']
    actions = ['analyst_review']
    required_action_groups = []
    if expected['values']==['unresolved']:
        gaps.append('customer_statement' if profile=='waiting' else 'other_unverified_material')
        if profile != 'waiting':
            actions += ['request_customer_evidence', 'verify_contact_channel', 'request_missing_window']
            required_action_groups.append(actions[1:])
        dispositions = ['continue_collection']
        stops = ['unavailable_evidence', 'budget_limit']
    else:
        dispositions = ['source_question_resolved']
        stops = ['sufficient_for_human_review']
        if 'unresolved' in expected['values']:
            dispositions.append('continue_collection');stops.append('unavailable_evidence')
            actions.append('request_customer_evidence')
    if counters:
        actions.append('preserve_timeline')
        required_action_groups.append(['preserve_timeline'])
    return dict(stage=world.stage, visible_sources=sorted(world.eligible()), full_state=expected,
        conditional_state=conditional+[choice('unresolved')], observations=facts,
        required_facts=needed, critical_evidence=evidence, required_counter_sources=list(counters),
        required_missing=gaps, allowed_actions=actions, required_action_groups=required_action_groups,
        allowed_dispositions=dispositions, allowed_stop_reasons=stops)


def evidence_group(source_id, status='available'):
    return [dict(source_id=source_id, status=status)]


def semantic_case(family, index, narrative, ordinal):
    snapshot, prior = candidate_case('evaluation', family, index, narrative, ordinal)
    target=snapshot['alert']['transaction_id']
    auth = next(r for r in snapshot['records'] if r['source_type']=='authentication')
    policy = next(r for r in snapshot['records'] if r['source_type']=='procedure')
    first = prior['stages'][1]['conditional_statement_rules']['first_source']
    last = prior['stages'][2]['conditional_statement_rules']['current_source']
    initial, final = narrative[2:]
    old_sha = digest(snapshot)
    auth_value = 'failure' if index==1 else 'success'
    auth['text']=f'模拟支付认证事件：{target} challenge_result={auth_value}。该字段仅记录挑战结果。'
    if index==3:
        auth['latency_minutes']=2
    roles={auth['source_id']:'authentication_event',policy['source_id']:'procedure'}
    if family=='representative_identity':
        roles[first]='contact_status' if index==2 else 'third_party_relay'
        if index==2:roles[last]='contact_status'
    snapshot.update(contract=CONTRACT, records_sha256=digest(snapshot['records']),
        stage_windows_minutes=[9+index, 11+index, 14+index])
    snapshot['query_rules']={r['source_id']:dict(business_role=roles.get(r['source_id'],'cardholder_statement'),
        expires_at=None,query_outcome='available') for r in snapshot['records']}
    world=SourceQueryWorld(snapshot); stages=[]; witness=[]
    auth_id,policy_id=auth['source_id'],policy['source_id']
    # One old reference is inherently ambiguous before order reconciliation.
    allowed_t1=['unresolved','denied'] if family=='correction_pointer' and index==0 else initial
    for i in range(3):
        if i:world.advance(i)
        new=[[auth_id,policy_id],[first],[last]][i]
        witness.append([new]);world.read(new)
        current=[None,first,last][i]
        value=['unresolved',allowed_t1,final][i]
        expected=choice(value,[current] if current else [])
        conditional=[];facts=[observation(auth_id,'authentication',auth_value),
                              observation(policy_id,'policy_version',policy['version'])]
        needed=[[auth_id,'authentication']]
        evidence=[evidence_group(auth_id)]
        counters=[]
        for source,label in [(first,allowed_t1),(last,final)][:i]:
            facts.append(observation(source,'customer_authorization',label))
            if family=='revocation_timing':
                facts.append(observation(source,'customer_retraction',label))
        if i:
            conditional.append(choice(value,[current],acquired=[current]))
            needed.append([current,'customer_authorization'])
            evidence.append(evidence_group(current))
        if i==2:
            conditional.append(choice(allowed_t1,[first],acquired=[first]))
            if initial in ('authorized','denied') and final in ('authorized','denied') and initial!=final:
                counters.append(first)
        stages.append(stage_reference(world,expected,conditional,facts,needed,evidence,counters=counters,
                                      profile='waiting' if i==0 else 'normal'))
    return snapshot, reference_for(snapshot,family,prior['template_id'],stages,witness,
                                   source=dict(kind='revised_v4_author_candidate',previous_runtime_digest=old_sha))


def query_case(entry, ordinal, extra_fallback_states=None):
    family, name, route, primary_text, fallback_text, corroboration, later, outcome, primary_state, follow, final=entry
    case_id='v31-eval-'+name; subject='simulated-'+case_id
    target,related=case_id+'-p',case_id+'-q'
    base=datetime(2026,7,1,10,0)+timedelta(days=ordinal)
    def at(minute):return (base+timedelta(minutes=minute)).isoformat()
    records=[];rules={}
    def add(key, source_type, role, text, minute, *, cost=0, latency=0, expires=None, result='available', channel=None, needs=()):
        identity='src-'+hashlib.sha256((case_id+':'+key).encode()).hexdigest()[:16]
        records.append(dict(source_id=identity,source_type=source_type,subject_id=subject,transaction_ids=[target,related],
            event_time=at(minute),available_at=at(minute),version=1,supersedes_id=None,origin=KIND,
            coverage=dict(start=at(0),end=at(minute),complete=True,note='Only the declared simulated source window'),
            text=text.format(P=target,Q=related),cost_units=cost,latency_minutes=latency,channel_ref=channel,requires=list(needs)))
        rules[identity]=dict(business_role=role,expires_at=at(expires) if expires is not None else None,query_outcome=result)
        return identity
    auth_value='failure' if ordinal%3==0 else 'success'
    auth=add('auth','authentication','authentication_event','模拟认证服务：{P} challenge_result='+auth_value+'。',4)
    routing=add('directory','account','identity_verification',route,13,channel='contact-directory',latency=ordinal%2)
    primary=add('recording','customer','cardholder_statement',primary_text,13,
        cost=2,latency=2+ordinal%2,result=outcome,channel='registered-callback',needs=[routing])
    alternate=add('attestation','customer','cardholder_statement',fallback_text,13,
        cost=4,latency=3+ordinal%3,channel='app-attestation',needs=[routing])
    follow_deadline=13+5+2*(ordinal%2) if family=='time_bound_records' else 22
    corroborate=add('archive','merchant','merchant_order',corroboration,13,
        cost=2,latency=3,expires=follow_deadline,channel='linked-record-archive',needs=[primary])
    last=add('correction','customer','cardholder_statement',later,33,channel='later-customer-reconciliation')
    snapshot=dict(contract=CONTRACT,case_id=case_id,data_kind=KIND,subject_id=subject,
        alert=dict(alert_id=case_id+'-alert',transaction_id=target,model_version='simulation_candidate',source_kind=KIND),
        cutoff=at(0),stage_times=[at(t) for t in (4,13,33)],stage_windows_minutes=[5+ordinal%3,10+ordinal%3,9+ordinal%4],
        transactions=[dict(transaction_id=t,user_id=subject,event_time=at(m),amount_cents=value,currency='CNY')
                      for t,m,value in [(target,0,6800+ordinal*250),(related,-4,4100+ordinal*190)]],
        access_budget_units=6, records=sorted(records,key=lambda r:r['source_id']),query_rules=rules)
    snapshot['records_sha256']=digest(snapshot['records'])
    follow_id=corroborate if follow=='corroborate' else alternate
    fallback_states={'recording_rotation':'unresolved','partial_invoice':'authorized',**(extra_fallback_states or {})}
    alternate_state=fallback_states.get(name,final)
    source=primary if follow=='corroborate' else alternate
    first_value=primary_state if follow=='corroborate' else alternate_state
    world=SourceQueryWorld(snapshot);stages=[]
    witness=[[[auth]],[[routing],[primary],[follow_id]],[[last]]]
    for i, operations in enumerate(witness):
        if i:world.advance(i)
        for ids in operations:world.read(ids)
        current=[None,source,last][i];value=['unresolved',first_value,final][i]
        expected=choice(value,[current] if current else [])
        conditions=[choice(value,[current],acquired=[current])] if current else []
        facts=[observation(auth,'authentication',auth_value)]
        needed=[[auth,'authentication']];evidence=[evidence_group(auth)]
        counters=[]
        if i:
            facts += [observation(primary,'customer_authorization',primary_state),
                      observation(alternate,'customer_authorization',alternate_state)]
            needed.append([current,'customer_authorization'])
            evidence.extend([evidence_group(primary,outcome),evidence_group(follow_id)])
            if i==2:
                facts.append(observation(last,'customer_authorization',final))
                conditions.append(choice(first_value,[source],acquired=[source]))
                evidence.append(evidence_group(last))
                if first_value != final:
                    counters.append(source)
                    needed.append([source,'customer_authorization'])
            other=alternate if source==primary else primary
            other_state=alternate_state if source==primary else primary_state
            conditions.append(choice(other_state,[other],acquired=[other]))
        stages.append(stage_reference(world,expected,conditions,facts,needed,evidence,counters=counters,
                                      profile='waiting' if i==0 else 'normal'))
    return snapshot,reference_for(snapshot,family,name,stages,witness,source=dict(kind='new_budgeted_query_case'))


def reference_for(snapshot,family,template,stages,witness,*,source):
    return dict(schema=REFERENCE,case_id=snapshot['case_id'],fold='evaluation',family=family,
        template_id=template,authorship='AI_author_reviewed_simulation',independent_person_reviewed=False,
        external_expert_reviewed=False,source=source,stages=stages,offline_reachability_witness=witness)


def candidates():
    for offset,(family, rows) in enumerate(list(EVALUATION_CANDIDATES.items())[:6]):
        for index,narrative in enumerate(rows):
            yield semantic_case(family,index,narrative,offset*5+index+1)
    for ordinal,entry in enumerate(QUERY_EVALUATION):
        yield query_case(entry,ordinal)


def build(output):
    output=Path(output)
    cases=list(candidates())
    output.mkdir(parents=True,exist_ok=False)
    (output/'runtime').mkdir();(output/'controller').mkdir()
    entries=[]
    for snapshot, reference in cases:
        case_id=snapshot['case_id']
        runtime=Path('runtime')/(case_id+'.json')
        control=Path('controller')/(case_id+'.json')
        write_new(output/runtime,snapshot);write_new(output/control,reference)
        entries.append(dict(case_id=case_id, subject_id=snapshot['subject_id'], family=reference['family'],
            template_id=reference['template_id'], provenance=reference['source']['kind'],
            runtime=runtime.as_posix(), reference=control.as_posix(),
            runtime_digest=digest(snapshot), reference_digest=digest(reference)))
    manifest=dict(schema='v31-query-evaluation-candidates-v1',cases=entries,counts=dict(Counter(
        entry['provenance'] for entry in entries)),review_scope='AI_author_reviewed_simulation',
        independent_person_reviewed=False,formal_model_run_authorized=False,old_90_cases_reused=False)
    write_new(output/'dataset.json',manifest)
    write_new(output/'dataset-sha256.json',dict(sha256=hashlib.sha256((output/'dataset.json').read_bytes()).hexdigest()))
    return manifest
