"""Paired case/family diagnostics; repeats are not additional independent cases."""
from collections import defaultdict
from decimal import Decimal
from random import Random
from statistics import mean


def value(row,metric):
    phases={p['stage']:p['assessment'] for p in row['phases']}
    if metric=='strict_trajectory':return float(len(phases)==3 and all(p['strict_author_reference_pass'] for p in phases.values()))
    if metric=='T2_target':return float(phases.get(2,{}).get('full_information_target_correct',False))
    if metric=='complete_delivery':return float(len(phases)==3 and all(p['delivered'] for p in phases.values()))
    if metric=='model_requests':return float(row['model_requests'])
    if metric=='committed_estimate_cny':
        amount=Decimal(row['estimated_cny'])+Decimal(row['risk_hold_cny'])
        if not amount.is_finite() or amount<0:raise ValueError('Finite nonnegative estimated cost required')
        return float(amount)
    raise ValueError('Predeclared metric required')


def quantile(ordered,p):
    at=(len(ordered)-1)*p;lo=int(at);hi=min(lo+1,len(ordered)-1)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(at-lo)


def paired_family_interval(plan,results,left,right,*,metric='strict_trajectory',replicates=2000,seed=20261102,level=.95):
    if type(replicates) is not int or not 1000<=replicates<=20000 or not 0<level<1:raise ValueError('Bounded predeclared resampling settings')
    if left==right or left not in plan['conditions'] or right not in plan['conditions']:raise ValueError('Distinct registered conditions')
    if len(set(plan['conditions']))!=len(plan['conditions']):raise ValueError('Distinct registered conditions')
    if type(plan['repeats']) is not int or plan['repeats']<2:raise ValueError('At least two preplanned repeats')
    cases={c['case_id']:c['family'] for c in plan['cases']}
    if len(cases)!=len(plan['cases']) or not cases:raise ValueError('Distinct registered cases')
    rows={}
    for row in results:
        if row['case_id'] not in cases or row['condition'] not in plan['conditions'] or type(row['repeat']) is not int or not 1<=row['repeat']<=plan['repeats']:raise ValueError('Unregistered result')
        key=row['case_id'],row['condition'],row['repeat']
        if key in rows:raise ValueError('Duplicate condition result; do not select a successful retry')
        if row['dataset_sha256']!=plan['dataset_sha256'] or row['protocol_sha256']!=plan['protocol_sha256']:raise ValueError('Frozen result fingerprint changed')
        stages=[p['stage'] for p in row['phases']]
        if len(stages)!=len(set(stages)) or not set(stages)<={0,1,2}:raise ValueError('Distinct stage records')
        for phase in row['phases']:
            assessment=phase['assessment']
            if type(phase['stage']) is not int or any(type(assessment.get(k)) is not bool for k in ('delivered','strict_author_reference_pass','full_information_target_correct')):raise ValueError('Boolean phase metrics required')
            if not assessment['delivered'] and (assessment['strict_author_reference_pass'] or assessment['full_information_target_correct']):raise ValueError('Undelivered report cannot pass business scoring')
        if type(row.get('model_requests')) is not int or row['model_requests']<0:raise ValueError('Nonnegative actual request count required')
        rows[key]=row
    missing=[(case,condition,repeat) for case in cases for condition in (left,right) for repeat in range(1,plan['repeats']+1) if (case,condition,repeat) not in rows]
    incomplete=[key for key,row in rows.items() if key[1] in (left,right) and len(row['phases'])!=3]
    summary=dict(metric=metric,left=left,right=right,paired_cases=len(cases),preplanned_repeats=plan['repeats'],
        independent_case_count=len(cases),missing_condition_runs=missing,incomplete_stage_runs=incomplete,
        full_sample_complete=not missing and not incomplete,agent_value_claim=False,
        nominal_level=level,interval_kind='paired family blocks with paired repeat resampling; descriptive, not coverage-certified',
        population_generalization=False)
    if missing or incomplete:
        summary.update(point=None,interval=None,reason='Do not infer from only successful/completed cases');return summary
    differences={case:[value(rows[(case,left,r)],metric)-value(rows[(case,right,r)],metric) for r in range(1,plan['repeats']+1)] for case in cases}
    families=defaultdict(list)
    for case,family in cases.items():families[family].append(case)
    family_means={family:mean(mean(differences[c]) for c in ids) for family,ids in families.items()}
    point=mean(family_means.values());summary.update(point=point,case_weighted_point=mean(mean(d) for d in differences.values()),
        family_count=len(families),family_effects=family_means,case_effects={c:mean(d) for c,d in differences.items()},
        point_weighting='equal family weight; cases equally weighted within family')
    if len(families)<2:
        summary.update(interval=None,leave_one_family_out=None,reason='Insufficient families for family resampling');return summary
    leave=[mean(v for family,v in family_means.items() if family!=omitted) for omitted in families]
    random=Random(seed);names=sorted(families);draws=[]
    for _ in range(replicates):
        selected=random.choices(names,k=len(names));sample=[]
        for family in selected:
            # Entire case family stays together. Only preplanned paired model
            # repeats are resampled within a case; failures are never filtered.
            sample.append(mean(mean(random.choices(differences[c],k=plan['repeats'])) for c in families[family]))
        draws.append(mean(sample))
    draws.sort();alpha=(1-level)/2
    summary.update(interval=[quantile(draws,alpha),quantile(draws,1-alpha)],leave_one_family_out=[min(leave),max(leave)],
        replicates=replicates,seed=seed,few_family_warning=len(families)<20,
        limitations=['Authored families are not a random sample of bank investigations','Few families do not justify nominal population coverage','No p-value or significance claim','Deterministic reuse is not an independent repeated observation'])
    return summary
