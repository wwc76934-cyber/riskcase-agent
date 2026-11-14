"""New controller-side authored candidates, unrelated to the old 90-case builder."""
from collections import Counter
from datetime import datetime,timedelta
import hashlib,json
from pathlib import Path
from app.investigation.contracts import digest
from app.investigation.v3.world import InvestigationWorld,KIND
from app.evaluation.v3_narratives import DEVELOPMENT,EVALUATION_CANDIDATES,ROUTING


def write_new(path,value):
    path=Path(path)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def case(fold,family,index,narrative,ordinal):
    case_id='v3-'+hashlib.sha256(('author-candidate:'+fold+':'+str(ordinal)).encode()).hexdigest()[:18];subject='fictional-'+case_id
    P,Q=case_id+'-payment-P',case_id+'-payment-Q'
    base=datetime(2026,1,1)+timedelta(days=ordinal+(0 if fold=='development' else 100))
    cutoff=base+timedelta(hours=10);times=[cutoff+timedelta(minutes=m) for m in (10,60,180)]
    t1,t2,value1,value2=narrative;t1=t1.format(P=P,Q=Q);t2=t2.format(P=P,Q=Q)
    cost_family=family in ('channel_verification','dependent_followup','deadline_and_unavailability')
    records=[]
    def add(suffix,kind,text,available,*,event=None,cost=0,latency=0,channel=None,requires=(),supersedes=None):
        identity='v3-src-'+hashlib.sha256((case_id+':'+suffix).encode()).hexdigest()[:20]
        record=dict(source_id=identity,source_type=kind,subject_id=subject,transaction_ids=[P,Q],
            event_time=(event or available).isoformat(),available_at=available.isoformat(),version=2 if supersedes else 1,
            supersedes_id=supersedes,origin=KIND,coverage=dict(start=cutoff.isoformat(),end=available.isoformat(),complete=True,note='Only declared simulated source coverage'),
            text=text,cost_units=cost,latency_minutes=latency,channel_ref=channel,requires=list(requires))
        records.append(record);return identity
    auth=add('authentication','authentication',f'模拟认证服务：{P} challenge_result=success；该日志没有记录持卡人的付款意愿。',times[0],event=cutoff)
    policy=add('procedure','procedure','项目模拟调查规范：认证结果不是持卡人意愿；逐笔记录来源声明，后来更正不得倒填历史。保留未确认结案，禁止自动关闭、认定欺诈、退款、冻结或改标签。',times[0])
    first=add('statement-T1','customer',t1,times[1])
    if cost_family:
        right='channel-A' if index%2==0 else 'channel-B';wrong='channel-B' if index%2==0 else 'channel-A'
        route=ROUTING[family][index].format(right=right,wrong=wrong,P=P,Q=Q)
        routing=add('contact-history','account',route,times[1],cost=1,latency=1)
        current=add('statement-T2-'+right,'customer',t2,times[2],cost=8,latency=6,channel=right,requires=[routing],supersedes=first)
        add('statement-T2-'+wrong,'customer','接听者未通过身份核验，只能复述别人的说法，不能形成持卡人对目标交易的确认。',times[2],cost=8,latency=6,channel=wrong,requires=[routing],supersedes=first)
    else:
        routing=None;current=add('statement-T2','customer',t2,times[2],supersedes=first)
    future=add('after-deadline','customer',f'仅供未来阶段的模拟补件：{P}后来被明确辨认。不得将本补件用于T0/T1/T2。',times[2]+timedelta(days=1))
    # Independent of role, authority, reference label and declaration order.
    records.sort(key=lambda r:r['source_id'])
    snapshot=dict(contract='payment-investigation-v3',case_id=case_id,data_kind=KIND,subject_id=subject,
        alert=dict(alert_id=case_id+'-alert',transaction_id=P,model_version='authored_fixture_ranking_NOT_trained_detector',source_kind=KIND),
        cutoff=cutoff.isoformat(),stage_times=[t.isoformat() for t in times],transactions=[
            dict(transaction_id=Q,user_id=subject,event_time=(cutoff-timedelta(minutes=10)).isoformat(),amount_cents=4000+ordinal,currency='CNY'),
            dict(transaction_id=P,user_id=subject,event_time=cutoff.isoformat(),amount_cents=9000+ordinal,currency='CNY')],
        records=records,records_sha256=digest(records),access_budget_units=15,stage_windows_minutes=[20,20,20])
    world=InvestigationWorld(snapshot);stages=[]
    for i,value in enumerate(('unresolved',value1,value2)):
        if i:world.advance(i)
        eligible=sorted(world.eligible())
        expected_observations=[dict(source_id=auth,predicate='authentication',transaction_id=P,value='success')]
        if i>=1:expected_observations.append(dict(source_id=first,predicate='customer_authorization',transaction_id=P,value=value1))
        if i==2:
            expected_observations.append(dict(source_id=current,predicate='customer_authorization',transaction_id=P,value=value2))
            if cost_family:
                unverified=next(r['source_id'] for r in records if r['channel_ref']==wrong)
                expected_observations.append(dict(source_id=unverified,predicate='customer_authorization',transaction_id=P,value='unresolved'))
        latest=first if i==1 else current if i==2 else None
        prior_counter=[first] if i==2 and value1 in ('authorized','denied') and value1!=value2 else []
        stages.append(dict(stage=i,allowed_sources=eligible,expected_source_statement=dict(predicate='customer_authorization',transaction_id=P,value=value),
            acquisition_prerequisites={r['source_id']:r['requires'] for r in records if r['source_id'] in eligible and r['requires']},
            cannot_confirm=['actual_fraud','legal_liability','confirmed_case_outcome'],
            critical_sources=[auth,policy]+([latest] if latest else [])+prior_counter,
            expected_observations=expected_observations,
            required_fact_source_ids=[auth]+([latest] if latest else [])+prior_counter,
            conditional_statement_rules=dict(default='unresolved',first_source=first if i>=1 else None,
                first_state=value1 if i>=1 else None,current_source=current if i==2 else None,current_state=value2 if i==2 else None),
            necessary_counterevidence='authentication_success_does_not_establish_authorization',
            necessary_counterevidence_sources=[auth]+prior_counter,
            required_missing=['confirmed_case_outcome']+(['customer_statement'] if i==0 else ['other_unverified_material'] if value=='unresolved' else []),
            acceptable_actions=['analyst_review','request_customer_evidence','request_missing_window','investigate_account','verify_policy_version','verify_contact_channel','preserve_timeline'],
            acceptable_dispositions=['continue_collection','escalate_for_review']+(['source_question_resolved'] if value in ('authorized','denied') else []),
            prohibited_dispositions=['close_alert_as_legitimate','confirm_fraud'],
            action_scope_note='Permitted read-only investigation suggestions, not an expert rating of escalation necessity or a license to execute financial actions.',
            prohibited_actions=['safe_close','confirmed_fraud','refund','freeze_account','write_fraud_label'],
            stop_conditions=['human_review_only','retain_uncertainty_when_source_unavailable'],
            reopen_on_new_material=True,reopen_conditions=['new_relevant_source','identity_or_scope_correction','material_contradiction']))
    reference=dict(case_id=case_id,fold=fold,family=family,template_id=family+'-'+str(index),author_reference=True,
        independent_person_reviewed=False,external_expert_reviewed=False,reference_status='author_candidate_not_formal_freeze',reference_schema='v3-author-reference-v2',
        task_categories=['progressive_arrival','natural_language_scope']+(['cost_bounded_followup'] if cost_family else []),
        stages=stages,never_visible_source_ids=[future],routing_required_source=routing,
        assessment_scope='Source-statement understanding and investigation support, NOT confirmed fraud or liability')
    return snapshot,reference


def build(output):
    output=Path(output)
    if output.exists():raise FileExistsError('New V3 candidate version required')
    output.mkdir(parents=True);(output/'runtime').mkdir();(output/'controller').mkdir();entries=[]
    for fold,families in [('development',DEVELOPMENT),('evaluation',EVALUATION_CANDIDATES)]:
        ordinal=0
        for family,narratives in families.items():
            for index,narrative in enumerate(narratives):
                ordinal+=1;snapshot,reference=case(fold,family,index,narrative,ordinal);identity=snapshot['case_id']
                runtime=output/'runtime'/f'{identity}.json';truth=output/'controller'/f'{identity}.json'
                write_new(runtime,snapshot);write_new(truth,reference)
                entries.append(dict(case_id=identity,fold=fold,family=family,subject_id=snapshot['subject_id'],
                    template_id=reference['template_id'],runtime=str(runtime.relative_to(output)),reference=str(truth.relative_to(output)),
                    runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),reference_sha256=hashlib.sha256(truth.read_bytes()).hexdigest()))
    body=dict(schema='v3-author-candidates-v1',entries=entries,counts=dict(Counter(e['fold'] for e in entries)),
        source_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),narratives_code_sha256=hashlib.sha256(Path(__file__).with_name('v3_narratives.py').read_bytes()).hexdigest(),
        independent_person_reviewed=False,formal_frozen=False,paid_model_runs=0,old_90_cases_reused=False)
    write_new(output/'dataset.json',body)
    write_new(output/'dataset-sha256.json',dict(sha256=hashlib.sha256((output/'dataset.json').read_bytes()).hexdigest()))
    return body
