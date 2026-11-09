"""B0: deterministic, evidence-scoped workflow. No reference or family access.

Rules handle explicit attribution, quoted speech, negation, payment scope and
source order. Unresolved language stays unresolved; this is a strong, inspectable
baseline, not a claim of general natural-language understanding.
"""
from collections import defaultdict
from datetime import datetime,timedelta
import re
from app.investigation.v3.output import AcquiredEvidence,PREDICATE_ROLES,OutputFailure
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.v31_output import V31ReviewContract
from app.investigation.v3.source_roles import customer_statement_role

UNVERIFIED=r'未通过.{0,6}身份|(?:还没|尚未|未曾|未).{0,4}核验.{0,4}身份|自称持卡人|接线员还没核验|不能形成持卡人'
UNCLEAR=r'无法确认|不能确认|不能辨认|仍不.{0,3}(?:确认|辨认)|尚未确认|未作.{0,4}确认|并没有回答|没(?:有)?说.{0,8}(?:不同意|否认|授权)|不是说|不是(?:没|未)|并非(?:没|未)|未(?:曾)?否认|不是我的(?:说法|原话)|不同意你说|不赞同你说|不表示|不等于.{0,8}(?:同意|否认)|只是.{0,5}(?:转述|转发)|仅.{0,5}转述|没有明确回答'
DENIED=r'未经我|未(?:曾|经我)?(?:许可|允许|同意|授权)|没(?:有)?(?:许可|允许|同意|授权)|不是我.{0,8}(?:操作|安排|授权|下单)|并非本人|不在.{0,5}(?:授权|许可|委托)|没有给.{0,6}(?:许可|授权)|不认可.{0,8}(?:付款|支付)'
AUTHORIZED=r'是我.{0,8}(?:同意|下|买|安排|授权)|我.{0,6}(?:事先|本人|亲自).{0,5}(?:同意|下单|买|安排|授权)|本人.{0,5}(?:下单|同意|安排)|由我.{0,5}委托|我许可|我允许|我同意|已获我许可|我明确授权'
SELF_QUOTE=r'我的(?:回答|意思|确认)|我(?:本人|明确|现在|郑重|这次|自己).{0,6}(?:确认|回答|说|同意|许可)'


def qualified(text):return re.search(UNVERIFIED,text) is None


def customer_state(text,target,other_ids,prior='unresolved'):
    if not qualified(text):return 'unresolved'
    if not re.search(SELF_QUOTE,text):text=re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"',' QUOTED ',text)
    focus=None;observed=[]
    for clause in re.split(r'[。！？!?；;，,\n]|但是|不过|但|然而',text):
        mentions=lambda identity:re.search(r'(?<![A-Za-z0-9_-])'+re.escape(identity)+r'(?![A-Za-z0-9_-])',clause) is not None
        target_here=mentions(target);other_here=any(mentions(identity) for identity in other_ids)
        if target_here:focus='target'
        elif other_here:focus='other'
        if focus!='target' or other_here and not target_here:continue
        if re.search(UNCLEAR,clause):observed.append('unresolved');continue
        deny=bool(re.search(DENIED,clause));allow=bool(re.search(AUTHORIZED,clause))
        if deny:observed.append('denied')
        elif allow and not deny:observed.append('authorized')
        elif re.search(r'维持(?:原来|之前)|仍按前|沿用先前|没有改(?:变|动)|不变',clause):observed.append(prior)
    return observed[-1] if observed else 'unresolved'


def originals(ledger):
    grouped=defaultdict(list)
    for handle,line in ledger.lines.items():grouped[line['source_id']].append((handle,line))
    return sorted(grouped.values(),key=lambda lines:(lines[0][1]['available_at'],lines[0][1]['version'],lines[0][1]['source_id']))


def machine_auth_observations(ledger):
    facts=[];pattern=re.escape(ledger.target_id)+r'\s+challenge_result=(success|failure)(?![A-Za-z_])'
    for lines in originals(ledger):
        if lines[0][1]['source_type'] not in PREDICATE_ROLES['authentication']:continue
        matches=[]
        for handle,line in lines:
            matches.extend((m.group(1),handle) for m in re.finditer(pattern,line['text']))
        if matches and len({value for value,_ in matches})==1:
            facts.append(dict(predicate='authentication',value=matches[0][0],refs=list(dict.fromkeys(h for _,h in matches))[:3]))
    return facts


def acquired_ids(port):
    return {r['source_id'] for e in port.evidence().values() for r in e['result']['data'].get('B',[])}


class SourceAccessFailure(OutputFailure):
    """Known tool/source failure, not a malformed model report."""


def checked_call(port,name,args):
    response=port.call(name,args)
    if not response['ok']:raise SourceAccessFailure(response['error']['code'])
    return response


def expiry_key(source):
    value=source.get('expires_at')
    return (value is None,datetime.fromisoformat(value) if value is not None else None)


def readable_batch(catalog,sources):
    if 'query_clock' not in catalog:
        return list(sources)
    clock=datetime.fromisoformat(catalog['query_clock']);minutes=catalog['remaining_minutes'];batch=[]
    for source in sorted(sources,key=lambda s:(expiry_key(s),s['source_id'])):
        duration=source['latency_minutes'];expiry=source['expires_at']
        finish=clock+timedelta(minutes=duration)
        if duration<=minutes and (expiry is None or finish<=datetime.fromisoformat(expiry)):
            batch.append(source);minutes-=duration;clock=finish
    return batch


def collect_free_and_prerequisites(port):
    context=checked_call(port,'v3_context',{});catalog=context['catalog']
    while True:
        known=acquired_ids(port)
        readable=[s for s in catalog['sources'] if s.get('readable',True)]
        free=[s for s in readable if s['source_id'] not in known and s['cost_units']==0 and set(s['requires'])<=known]
        if free:
            free=readable_batch(catalog,sorted(free,key=lambda s:s['source_id']))
            if free:
                catalog=checked_call(port,'v3_read',dict(source_ids=[s['source_id'] for s in free[:12]]))['catalog'];continue
        needed={r for s in readable if s['source_id'] not in known for r in s['requires'] if r not in known}
        prereq=[s for s in readable if s['source_id'] in needed and set(s['requires'])<=known]
        if 'query_clock' in catalog:
            prereq=[s for s in prereq if s['cost_units']<=catalog['remaining_cost_units'] and s['latency_minutes']<=catalog['remaining_minutes']]
        if not prereq:break
        candidate=min(prereq,key=lambda s:(expiry_key(s),s['cost_units'],s['latency_minutes'],s['source_id']))
        if candidate['cost_units']>catalog['remaining_cost_units'] or candidate['latency_minutes']>catalog['remaining_minutes']:break
        catalog=checked_call(port,'v3_read',dict(source_ids=[candidate['source_id']]))['catalog']
    return dict(frame=context['frame'],catalog=catalog)


def compile_ledger(port,context):
    frame=context['frame'];rows=frame['data']['rows'];subjects={r['user_id'] for r in rows}
    if len(subjects)!=1:raise ValueError('Single subject scope required')
    return AcquiredEvidence(case_id=frame['case_id'],cutoff=frame['cutoff'],as_of=context['catalog']['as_of'],
        subject_id=next(iter(subjects)),transaction_ids=[r['transaction_id'] for r in rows],target_id=frame['data']['alert']['transaction_id']).ingest(port.evidence())


def route_score(channel,ledger):
    if not channel:return 0
    score=0
    for lines in originals(ledger):
        if lines[0][1]['source_type']!='account':continue
        text='\n'.join(line['text'] for _,line in lines)
        for clause in re.split(r'[。；;，,\n]',text):
            if channel not in clause:continue
            if re.search(r'未通过|未核验|不可|不能|排除|失效|过期|不适用|自称|转述',clause):score-=3
            elif re.search(r'已核|核验通过|本人|原登记|可信|验证完成|可用|有效',clause):score+=2
    return score


def collect_rule_workflow(port):
    context=collect_free_and_prerequisites(port);catalog=context['catalog'];ledger=compile_ledger(port,context)
    pending=[s for s in catalog['sources'] if s['source_id'] not in acquired_ids(port)]
    pending.sort(key=lambda s:(expiry_key(s),-route_score(s['channel_ref'],ledger),-s['version'],s['cost_units'],s['latency_minutes'],s['source_id']))
    for item in pending:
        if 'query_clock' in catalog and not any(s['source_id']==item['source_id'] and s['readable'] for s in catalog['sources']):continue
        if not set(item['requires'])<=acquired_ids(port):continue
        if item['cost_units']<=catalog['remaining_cost_units'] and item['latency_minutes']<=catalog['remaining_minutes']:
            catalog=checked_call(port,'v3_read',dict(source_ids=[item['source_id']]))['catalog']
    return compile_ledger(port,dict(frame=context['frame'],catalog=catalog))


def rule_report(ledger, *, study=False):
    factory=V31ReviewContract if study else TypedReviewContract
    contract=factory(ledger.target_id);facts=machine_auth_observations(ledger);state='unresolved';selected=[]
    others=set(ledger.transaction_ids)-{ledger.target_id}
    for lines in originals(ledger):
        if lines[0][1]['source_type'] not in PREDICATE_ROLES['customer_authorization']:continue
        text='\n'.join(line['text'] for _,line in lines)
        role_allowed=customer_statement_role(lines[0][1])
        value=customer_state(text,ledger.target_id,others,prior=state) if role_allowed else 'unresolved'
        facts.append(dict(predicate='customer_authorization',value=value,refs=[h for h,_ in lines][:3]))
        if role_allowed and qualified(text):state=value;selected=[len(facts)]
    if study:
        facts.extend(dict(predicate='policy_version',refs=[lines[0][0]]) for lines in originals(ledger)
                     if lines[0][1]['source_type']=='procedure' and ledger.target_id in lines[0][1]['transaction_ids'])
    extraction=contract.extract(dict(facts=facts),ledger)
    missing=['other_unverified_material'] if state=='unresolved' and any(f['predicate']=='customer_authorization' for f in facts) else ['customer_statement'] if state=='unresolved' else []
    payload=dict(investigation=dict(hypotheses=[dict(description='Deterministic interpretation of acquired source statements; actual fraud remains unconfirmed',status='open',
        support=['F'+str(i) for i in selected],counterevidence=[],unknowns=['Confirmed fraud outcome'])],tensions=[],timeline_refs=list(ledger.events),
        recommendations=[dict(action='analyst_review',fact_ids=[],reason='Source interpretation requires analyst review')],missing=missing,
        stop_reason='sufficient_for_human_review' if state!='unresolved' else 'unavailable_evidence',summary='B0 rule workflow; no model, bank action or confirmed fraud finding'),
        source_statement=dict(value=state,fact_ids=['F'+str(i) for i in selected],rationale='Latest qualified source interpreted by declared attribution/negation/scope rules'),
        proposed_disposition='source_question_resolved' if state!='unresolved' else 'continue_collection')
    if study:
        investigation=payload['investigation']
        current=set(payload['source_statement']['fact_ids'])
        contradictory=[key for key,fact in extraction['facts'].items() if key not in current
                       and fact['predicate']=='customer_authorization' and fact['value'] not in (state,'unresolved')]
        if contradictory:
            investigation['hypotheses'][0]['counterevidence']=contradictory
            investigation['recommendations'].append(dict(action='preserve_timeline',fact_ids=contradictory,
                reason='Preserve earlier conflicting customer statements with their dates'))
        if state=='unresolved':
            eligible=[key for key,fact in extraction['facts'].items() if fact['predicate']=='customer_authorization']
            if eligible:
                investigation['recommendations'].append(dict(action='verify_contact_channel' if any(
                    receipt['status']=='unavailable' for receipt in ledger.query_results.values()) else 'request_customer_evidence',
                    fact_ids=eligible,reason='A qualified target-payment statement remains unresolved'))
            if any(receipt['status']=='expired' for receipt in ledger.query_results.values()):
                investigation['missing'].append('other_unverified_material')
    report=contract.assemble(payload,extraction,ledger);report['observation_producer']='deterministic_rules_and_literal_machine_fields'
    return report
