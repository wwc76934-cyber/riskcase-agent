"""B0: all available evidence, generic extraction and conservative workflow rules.

No family IDs, controller truth or evaluation files are read. This is code,
not model planning; expose extraction limitations rather than relabel results.
"""
import re
from app.investigation.fraud_world import originals


def report_from_observations(observations):
    records={};target=None;a_rows=[]
    for e in observations:
        if not e.get('ok') or not e.get('evidence_id'):continue
        ref=e['evidence_id'];result={k:e[k] for k in ('source','data')}
        for identity,record in originals(result).items():records[identity]=(ref,record)
        if 'A' in e['data']:a_rows=e['data']['A']['rows']
        if 'target' in e['data']:target=(ref,e['data']['target'])
    if target is None:raise ValueError('Bound target not read')
    claims=[];details={};refs_by_predicate={}
    def claim(ref,record,predicate,value):
        # Quote the original body line; never invent a normalized source quote.
        body=[l for l in record['lines'] if not l['text'].startswith(('【','关联交易','Record type:','来源：','原始说明：','正文','此导出','引用时'))
              and '导出条目 / version ' not in l['text']]
        line=body[0] if body else record['lines'][0]
        if not body:
            line=next((l for l in record['lines'] if l['text'].startswith(('记录员说明：','原始说明：'))),line)
        identity='O'+str(len(claims)+1)
        claims.append(dict(id=identity,predicate=predicate,value=value,citations=[dict(evidence_id=ref,source_id=record['source_id'],lines=[line['number']],quote=line['text'][:800])]))
        details[identity]=(ref,record);refs_by_predicate.setdefault(predicate,[]).append(identity)
    for identity,(ref,record) in records.items():
        text='\n'.join(l['text'] for l in record['lines']);kind=record['source_type']
        if kind=='authentication':
            value=('not_established' if not record['coverage']['complete'] else
                   'failure' if re.search('未通过|认证失败|result=failure',text) else
                   'success' if re.search('流程通过|result=success|returned OK|推送确认|挑战记录为通过',text) else 'not_established')
            claim(ref,record,'authentication',value)
        elif kind=='device':
            value='alias_only' if re.search('硬件键完全一致|仅是名称更新',text) else 'unchanged' if re.search('没有新增硬件|已有八个月',text) else 'changed'
            claim(ref,record,'device',value)
        elif kind=='account':
            if '待关联事件索引' in text:claim(ref,record,'related_events',max(0,len(record['transaction_ids'])-1))
            else:claim(ref,record,'account_contact','unchanged' if '未记录联系渠道变更' in text else 'changed' if re.search('变更|重新绑定',text) else 'unknown')
        elif kind=='customer':
            if re.search('撤回.*否认',text) and '确认' in text:claim(ref,record,'customer_retraction','authorized')
            else:
                current=text
                # Generic correction/speaker boundaries, not case IDs or gold.
                for marker in ('核对当前交易号后','随后客户本人','客户更正','关于当前这笔','客户本人'):
                    if marker in current:current=current.rsplit(marker,1)[-1]
                positive=re.search('事先明确授权|确认这笔交易|由我本人授权|确认旅途中授权|明确表示已授权|明确同意|确认同意当前付款|同意当前付款',current)
                negative=re.search('未.{0,12}授权|未经.{0,12}同意|未同意|未.{0,8}批准|没有.{0,12}授权|没有同意|没有批准|不承认授权|未经许可|否认授权|否认同意|不在.{0,16}同意.{0,16}范围|从未获得.{0,8}同意',current)
                if positive or negative:claim(ref,record,'customer_authorization','denied' if negative else 'authorized')
                elif re.search('不确定|记不清',current):claim(ref,record,'customer_authorization','unresolved')
        elif kind=='merchant':claim(ref,record,'merchant_statement','unverified')
        elif kind=='procedure' and record['effective_at_investigation'] and not record['superseded_by_available']:
            claim(ref,record,'policy_version',record['version'])
    if not claims:raise ValueError('No interpretable originals')
    def values(predicate):return [c['value'] for c in claims if c['predicate']==predicate]
    auth=values('authentication');customer=values('customer_retraction') or values('customer_authorization')
    missing=['confirmed_case_outcome'];actions=[]
    def recommend(action,reason,predicates,priority='normal'):
        refs=sorted({details[identity][0] for p in predicates for identity in refs_by_predicate.get(p,[])}) or [target[0]]
        actions.append(dict(action=action,reason=reason,evidence_ids=refs,priority=priority))
    if not auth or 'not_established' in auth:
        missing.append('authentication_record_as_of' if not auth else 'authentication_window')
        recommend('request_missing_window','取得覆盖目标时段的认证原件，当前缺失不能按无异常处理。',['authentication'])
    if not customer or customer[-1]!='authorized':
        if not customer:missing.append('customer_statement')
        recommend('request_customer_evidence','核验当前交易的持卡人授权及联系渠道；认证通过不替代授权声明。',['customer_authorization','authentication'])
    for _,record in records.values():
        if record['source_type']=='account':
            text='\n'.join(l['text'] for l in record['lines'])
            if re.search('身份.*(?:待核验|未核验)|尚未核验.*身份',text):
                missing.append('sim_change_identity' if 'SIM' in text else 'contact_change_identity')
    if 'changed' in values('account_contact') or ('changed' in values('device') and (not customer or customer[-1]!='authorized')) or values('related_events'):
        recommend('investigate_account','核查设备、联系渠道或关联事件的操作者，保留逐笔授权边界。',['account_contact','device','related_events'],'high')
    if not actions:recommend('analyst_review','当前材料可整理提交人工审阅，尚无已确认外部结案。',['customer_authorization','policy_version'])
    tensions=[]
    def tension(kind,predicates,text):
        ids=[identity for p in predicates for identity in refs_by_predicate.get(p,[])]
        if ids:tensions.append(dict(kind=kind,observation_ids=ids[:6],explanation=text))
    if 'success' in auth and 'denied' in values('customer_authorization'):
        tension('authentication_is_not_authorization',['authentication','customer_authorization'],'流程成功与客户否认可以同时成立，不能据此单独判定授权或欺诈。')
    if 'not_established' in auth:tension('coverage_gap',['authentication'],'该导出不足以覆盖目标认证事实，缺条目不是未发生。')
    if values('customer_retraction'):tension('statement_version_change',['customer_authorization','customer_retraction'],'保留原声明及后来可得的修订，不能改写原来的记录。')
    if values('related_events') and 'denied' in values('customer_authorization'):
        tension('transaction_specific_authorization',['related_events','customer_authorization'],'此前购买的授权不能自动覆盖当前交易。')
    if any('SYSTEM OVERRIDE' in '\n'.join(l['text'] for l in r['lines']) for _,r in records.values()):
        tension('prompt_injection_ignored',['merchant_statement'],'商户原件中的命令被作为不可信内容保留，没有执行权限。')
    timeline=[dict(event_time=r['event_time'],source_id=r['source_id'],evidence_id=ref,layer='B') for ref,r in records.values()]
    linked_ids={i for _,record in records.values() for i in record['transaction_ids']}|{target[1]['transaction_id']}
    for row in ([r for r in a_rows if r['transaction_id'] in linked_ids] if a_rows else [target[1]]):
        ref=next((o['evidence_id'] for o in observations if o.get('ok') and 'A' in o.get('data',{})),target[0])
        timeline.append(dict(event_time=row['event_time'],source_id=row['transaction_id'],evidence_id=ref,layer='A'))
    timeline=sorted(timeline,key=lambda x:(x['event_time'],x['source_id']))[:12]
    support=[c['id'] for c in claims if c['value'] in ('denied','changed')]
    counter=[c['id'] for c in claims if c['value'] in ('authorized','unchanged','alias_only')]
    return dict(status='awaiting_analyst_review',observations=claims,hypotheses=[dict(id='H1',
        description='当前交易是否仍有未解决的授权或账户安全线索',status='unresolved',support=support[:8],counterevidence=counter[:8],unknowns=missing)],
        tensions=tensions,timeline=timeline,recommendations=actions[:4],missing=list(dict.fromkeys(missing)),
        stop_reason='unavailable_evidence' if any(a['action']!='analyst_review' for a in actions) else 'sufficient_for_human_review',
        summary='确定性基线整理全部可得原件，使用通用文本匹配与保守规则提出待审阅事项。来源声明不是已确认欺诈结果；复杂措辞的抽取可能有误，需人工核对。')


class BaselineProbeClient:
    """A simulated model interface for transport tests, not B1/B2 model results."""
    simulation=True
    def chat(self,messages,tools=None):
        import json
        frame=json.loads(messages[1]['content'])
        observations=frame['observations']+[json.loads(m['content']) for m in messages if m['role']=='tool']
        def plan():
            args=dict(revision=frame['hypothesis_revision'],hypotheses=[dict(id='H1',description='Transport probe: authorization remains unadjudicated',status='unresolved',support=[],counterevidence=[],unknowns=['External outcome unknown'])],next_sources=['authentication'],decision_note='Scripted protocol probe; this is not model planning evidence')
            return dict(role='assistant',content=None,tool_calls=[dict(id='probe-plan-'+str(args['revision']),type='function',function=dict(name='update_hypotheses',arguments=json.dumps(args)))])
        if not frame['final_only'] and frame['hypothesis_revision']==0:return plan()
        if not any('B' in o.get('data',{}) for o in observations):
            return dict(role='assistant',content=None,tool_calls=[dict(id='probe-read',type='function',function=dict(name='world_materials',arguments='{}'))])
        if not frame['final_only'] and frame['hypothesis_revision']<2:return plan()
        return dict(role='assistant',content=json.dumps(report_from_observations(observations)))
