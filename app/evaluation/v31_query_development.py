"""Two authored mechanism fixtures. Kept outside any evaluation fold."""
from datetime import datetime,timedelta
from hashlib import sha256

from app.investigation.contracts import digest
from app.investigation.v3.query_world import CONTRACT,SourceQueryWorld
from app.investigation.v3.world import KIND


def callback_and_capture(*,callback_available):
    """Shared first decision, different useful follow-up after the query."""
    variant='primary-recognized' if callback_available else 'primary-unavailable'
    case_id='v31-dev-'+variant
    subject='simulated-subject-'+variant
    target,related=(case_id+'-p',case_id+'-q')
    base=datetime(2026,6,3,10,0)
    starts=(4,11,31)
    at=lambda minute:(base+timedelta(minutes=minute)).isoformat()
    records=[];rules={}

    def source(label,kind,role,text,minute,*,cost=0,latency=0,expiry=None,channel=None,
               outcome='available',requires=(),previous=None,transactions=None):
        identity='src-'+sha256((case_id+':'+label).encode()).hexdigest()[:14]
        records.append(dict(source_id=identity,source_type=kind,subject_id=subject,
            transaction_ids=transactions or [target,related],event_time=at(minute),available_at=at(minute),
            version=2 if previous else 1,supersedes_id=previous,origin=KIND,
            coverage=dict(start=at(0),end=at(minute),complete=True,note='Simulated source window only'),
            text=text,cost_units=cost,latency_minutes=latency,channel_ref=channel,requires=list(requires)))
        rules[identity]=dict(business_role=role,expires_at=at(expiry) if expiry is not None else None,
                             query_outcome=outcome)
        return identity

    auth=source('authentication','authentication','authentication_event',
        f'支付 {target} 的校验 challenge_result=success。设备完成挑战，日志未记录付款意愿。',
        4,transactions=[target])
    route=source('route','account','identity_verification',
        '登记号码完成过设备校验，可调已核验回呼。回呼记录取不到时，可以申请应用内认证回执。'
        '回呼提及预授权和扣款混用时，可调商户的分笔入账单。',
        11,channel='routing')
    source('relay','customer','third_party_relay',
        '来电人自称代持卡人处理，转述“那笔我点过”。他未提供时间，也不清楚持卡人指预授权还是最后扣款。',
        11,channel='relay')
    primary=source('primary','customer','cardholder_statement',
        f'完成身份核验。持卡人说“我同意了 {target} 的预授权。短信后来又来了一条，我没同意那笔扣款”。'
        '她将同一商户的两条通知都称作“那笔”，请求核对实际入账。',
        11,cost=2,latency=2,channel='registered-callback',requires=[route],
        outcome='available' if callback_available else 'unavailable')
    merchant=source('capture','merchant','merchant_order',
        f'商户原件。{target} 是预授权；独立入账 {related} 使用另一订单序号，没有从 {target} 承接支付授权。',
        11,cost=2,latency=3,expiry=19,channel='capture-ledger',requires=[primary])
    fallback=source('alternate','customer','cardholder_statement',
        f'应用内核验回执。持卡人更正“我认可的是 {related}；我没有同意 {target}，也没有授权家人代付它。”',
        11,cost=4,latency=4,channel='app-attestation',requires=[route])
    last=source('later-statement','customer','cardholder_statement',
        (f'补件。持卡人已核对订单号。“我同意 {target} 的预授权；对 {related} 的争议另行登记。”'
         if callback_available else
         f'补件。持卡人核对后重申没有授权 {target}。她撤回的是关于 {related} 的争议陈述。'),
        31,previous=primary if callback_available else fallback,channel='followup')
    snapshot=dict(contract=CONTRACT,case_id=case_id,data_kind=KIND,subject_id=subject,
        alert=dict(alert_id=case_id+'-alert',transaction_id=target,model_version='simulation_seed',source_kind=KIND),
        cutoff=at(0),stage_times=[at(m) for m in starts],stage_windows_minutes=[6,12,12],
        transactions=[dict(transaction_id=t,user_id=subject,event_time=at(event),amount_cents=amount,currency='CNY')
                      for t,event,amount in [(target,0,7200),(related,-1,9600)]],
        records=sorted(records,key=lambda item:item['source_id']),access_budget_units=6,query_rules=rules)
    snapshot['records_sha256']=digest(snapshot['records'])
    SourceQueryWorld(snapshot)
    reference=dict(case_id=case_id,fold='mechanism_development',author_reference=True,
        independently_reviewed=False,required_T1_queries=[primary,merchant if callback_available else fallback],
        T1_customer_state='authorized' if callback_available else 'denied',
        required_T2_original=last,authentication_not_intent_source=auth,
        terminal_actions_forbidden=['safe_close','confirm_fraud','refund','freeze_account'])
    return snapshot,reference
