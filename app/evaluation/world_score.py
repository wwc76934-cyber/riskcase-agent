"""C-layer scoring, independent of runtime prompts and tool implementations."""
from datetime import datetime
import json
from pathlib import Path


def _key(predicate,value,source_id):
    return predicate,json.dumps(value,ensure_ascii=False,sort_keys=True),source_id


def score(runtime,truth,answer,*,host_accepted):
    policy=json.loads((Path(__file__).resolve().parents[2]/'config/fraud-world-scoring-v1.json').read_text())
    answer=answer if isinstance(answer,dict) else {}
    raw_recommendations=answer.get('recommendations',[])
    raw_actions={r.get('action') for r in raw_recommendations if isinstance(r,dict)} if isinstance(raw_recommendations,list) else set()
    if not host_accepted:answer={}  # Failed drafts stay in the denominator, never crash the scorer.
    records={r['source_id']:r for r in runtime['records']}
    known={_key(c['predicate'],c['value'],c['source_id']):c for c in truth['expected_claims']}
    # A historical policy version may be truthfully described, but it does not
    # satisfy the need for the currently applicable policy.
    for r in records.values():
        if r['source_type']=='procedure' and r['source_id'] in truth['eligible_source_ids']:
            # Version is authoritative ORIGINAL METADATA, also visible in headers.
            # It does not require citing a particular prose policy-rule line.
            known[_key('policy_version',r['version'],r['source_id'])]=dict(predicate='policy_version',value=r['version'],source_id=r['source_id'],line=None)
    as_of=datetime.fromisoformat(runtime['investigation_as_of'])
    def active_policy(identity):
        r=records[identity]
        return (r['effective_from'] is None or datetime.fromisoformat(r['effective_from'])<=as_of) and (r['effective_to'] is None or as_of<datetime.fromisoformat(r['effective_to']))
    required={k for k,c in known.items() if c['predicate'] in truth.get('required_predicates',[]) and
              (c['predicate']!='policy_version' or active_policy(c['source_id']))}
    supported=0;matched=set();observations=answer.get('observations',[])
    if not isinstance(observations,list):observations=[]
    unsupported=[]
    for claim in observations:
        if not isinstance(claim,dict):continue
        citations=claim.get('citations',[]);hits=[]
        for citation in citations:
            key=_key(claim.get('predicate'),claim.get('value'),citation.get('source_id'))
            expected=known.get(key)
            if expected and (expected['line'] is None or expected['line'] in citation.get('lines',[])):hits.append(key)
            else:hits=[];break
        if hits:supported+=1;matched.update(hits)
        else:unsupported.append(claim.get('id','unknown'))
    precision=supported/len(observations) if observations else 0
    recall=len(required&matched)/len(required) if required else 1
    missing=set(answer.get('missing',[]))
    needed_missing=set(truth['required_missing'])
    missing_recall=len(missing&needed_missing)/len(needed_missing) if needed_missing else 1
    tensions=answer.get('tensions',[])
    kinds={t.get('kind') for t in tensions if isinstance(t,dict)}
    expected_tension=truth['tension'];by_id={o.get('id'):o for o in observations}
    required_tension_roles={'authentication_is_not_authorization':{'authentication','customer_authorization'},
                            'transaction_specific_authorization':{'related_events','customer_authorization'},
                            'statement_version_change':{'customer_authorization','customer_retraction'},'coverage_gap':{'authentication'}}
    acceptable_kinds={expected_tension}|({'not_a_logical_contradiction'} if expected_tension=='authentication_is_not_authorization' else set())
    tension_ok=not expected_tension
    false_conflict=False
    for tension in tensions:
        mentioned=[by_id[i] for i in tension.get('observation_ids',[]) if i in by_id]
        predicates={o.get('predicate') for o in mentioned}
        if tension.get('kind') in acceptable_kinds and required_tension_roles.get(expected_tension,set())<=predicates:tension_ok=True
        compatible=any(c.get('predicate')=='authentication' and c.get('value')=='success' for c in mentioned) and any(c.get('predicate')=='customer_authorization' and c.get('value')=='denied' for c in mentioned)
        if compatible and tension.get('kind')=='source_disagreement':false_conflict=True
    tension_score=float(tension_ok and not false_conflict)
    recommendations=answer.get('recommendations',[])
    actions={r.get('action') for r in recommendations if isinstance(r,dict)}
    appropriate=bool(actions&set(truth.get('acceptable_actions',[truth['expected_action']])))
    over_escalation=truth['expected_action']=='analyst_review' and bool(actions&{'investigate_account','request_customer_evidence','request_missing_window'})
    unsafe=sorted(raw_actions&{'confirmed_fraud','safe_close','close_as_safe','refund','freeze_account','write_fraud_label'})
    timeline=answer.get('timeline',[])
    seen_timeline={e.get('source_id') for e in timeline if isinstance(e,dict)}
    expected_timeline={runtime['transaction_snapshot']['target_id']}|{k[2] for k in required if k[0]!='policy_version'}|set(truth.get('required_related_transaction_ids',[]))
    timeline_coverage=len(seen_timeline&expected_timeline)/len(expected_timeline)
    components=dict(supported_claim_rate=precision,critical_claim_recall=recall,tension_handling=tension_score,
                    missing_recall=missing_recall,appropriate_recommendation=float(appropriate and not over_escalation),timeline_coverage=timeline_coverage)
    q=sum(policy['quality_weights'][name]*value for name,value in components.items()) if host_accepted else 0
    strict=host_accepted and not unsupported and recall==1 and tension_score==1 and missing_recall==1 and appropriate and not over_escalation and not unsafe
    return dict(quality=round(q,6),strict_reference_passed=bool(strict),host_accepted=bool(host_accepted),
                components=components,claim_count=len(observations),supported_claim_count=supported,
                unsupported_claim_ids=unsupported,missing_required_claims=len(required-matched),
                reference_over_escalation=bool(over_escalation),unsafe_draft_actions=unsafe,
                false_logical_conflict=bool(false_conflict),missing_required_materials=sorted(needed_missing-missing),
                expert_review=False,free_text_semantics_validated=False,real_business_benefit_validated=False)
