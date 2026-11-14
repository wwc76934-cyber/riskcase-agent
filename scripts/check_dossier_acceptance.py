"""Check the predeclared demo gate on saved real-model results; no model requests."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def stable_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--previous-bundle',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();rows=json.loads((a.results/'results.json').read_text(encoding='utf-8'))
    manifest=json.loads((a.bundle/'manifest.json').read_text(encoding='utf-8'))
    cases={c['id']:c for c in json.loads((a.bundle/'cases.json').read_text(encoding='utf-8')) if c['id'] in manifest['live_ids']}
    expected={(case,strategy) for case in cases for strategy in ('all_context','adaptive')}
    if len(rows)!=len(expected) or {(r['case'],r['strategy']) for r in rows}!=expected:raise ValueError('Incomplete or duplicated experiment')
    if any(r['client_kind']!='real_LLM' for r in rows):raise ValueError('Fixtures cannot satisfy model gate')
    arms={}
    for strategy in ('all_context','adaptive'):
        own=[r for r in rows if r['strategy']==strategy]
        passed=sum(r['score']['task_passed'] for r in own)
        facts=sum(r['score']['values_correct'] for r in own)
        coverage=[];human=[]
        for r in own:
            result=json.loads((a.results/r['case']/strategy/'result.json').read_text(encoding='utf-8'))
            if cases[r['case']]['gold']['values']['coverage']=='incomplete':
                coverage.append(bool(result['answer'] and result['answer']['verdict']=='request_records' and r['score']['task_passed']))
            human.append(result['state']=='awaiting_review' and result['validation']['semantic_validation'] is False and result['validation']['human_review_required'])
        pair=all(next(r for r in own if r['case']==case)['score']['task_passed'] for case in ('raw-07','raw-08'))
        gate=passed>=5 and facts/(4*len(own))>=.9 and all(coverage) and pair and all(human)
        arms[strategy]=dict(demo_gate_passed=gate,n=len(own),passed=passed,facts_correct=facts,facts_total=4*len(own),
            coverage_cases_passed=all(coverage),policy_pair_passed=pair,human_review_required=all(human),
            first_draft_passed=sum(r['first_draft_score']['task_passed'] for r in own),
            draft_review_failures=sum(r['draft_review_failures'] for r in own),
            requests=sum(r['model_requests'] for r in own),mean_originals=sum(r['originals_read'] for r in own)/len(own),
            estimated_cny=str(sum(Decimal(r['estimated_cny']) for r in own)))
    total=sum(Decimal(r['estimated_cny']) for r in rows)
    budget_ok=total<=Decimal('2') and sum(r['model_requests'] for r in rows)<=72 and not any(r['unknown_cost_requests'] for r in rows)
    comparison={}
    if a.previous_bundle:
        prior={c['id']:c for c in json.loads((a.previous_bundle/'cases.json').read_text(encoding='utf-8')) if c['id'] in cases}
        bodies=lambda group:{key:{d['id']:d['body'] for d in c['snapshot']['documents']} for key,c in group.items()}
        gold=lambda group:{key:c['gold'] for key,c in group.items()}
        comparison=dict(fact_originals_unchanged=stable_hash(bodies(prior))==stable_hash(bodies(cases)),
                        gold_unchanged=stable_hash(gold(prior))==stable_hash(gold(cases)),
                        prior_bundle=str(a.previous_bundle))
    reduction=1-arms['adaptive']['mean_originals']/arms['all_context']['mean_originals']
    value=dict(batch_id=manifest['batch_id'],demo_gate_passed=budget_ok and all(v['demo_gate_passed'] for v in arms.values()),
        arms=arms,budget_ok=budget_ok,estimated_cny=str(total),original_read_reduction=reduction,
        cost_ratio_adaptive_to_all_context=str(Decimal(arms['adaptive']['estimated_cny'])/Decimal(arms['all_context']['estimated_cny'])),
        recommendation='all_context_for_bounded_small_dossiers',source_comparison=comparison,
        production_validated=False,independent_generalization_validated=False,
        limitation='Six author-generated cases already observed during repairs; this gate is a bounded demonstration, not external validation.')
    a.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
