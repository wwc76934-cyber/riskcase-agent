"""Report frozen outcomes, budget holds and paired paths without selecting wins."""
from collections import Counter,defaultdict
from decimal import Decimal
import json
from pathlib import Path
import random
import sqlite3
import statistics

from app.evaluation.world_runner import checked_dataset
from app.investigation.world_service import WorldService
from app.investigation.fraud_world import originals
from app.risk.history import sha256_file
from app.service.runtime import write_json


def mean(values):return sum(values)/len(values) if values else None


def family_bootstrap(deltas,*,seed=20261101,replicates=4000):
    """Cluster on authored scenario families, not IID near-duplicate rows."""
    groups=defaultdict(list)
    for family,value in deltas:groups[family].append(value)
    if not groups:return None
    rng=random.Random(seed);names=sorted(groups);draws=[]
    for _ in range(replicates):
        sampled=[v for family in rng.choices(names,k=len(names)) for v in groups[family]]
        draws.append(mean(sampled))
    draws.sort();return dict(low=draws[int(.025*replicates)],high=draws[int(.975*replicates)],clusters=len(names),replicates=replicates,
                             interpretation='Internal author-family uncertainty, not an external population confidence guarantee')


def _behavior(service,entry):
    row=service.store.get(entry['job']);state=service.store.load(entry['job']) or {};spec=row['spec'];world=spec['snapshot']
    by_id={r['source_id']:r['source_type'] for r in world['records']};visits=[];read=set()
    for trace in state.get('trace',[]):
        if not trace.get('ok'):continue
        name=trace['tool'];args=json.loads(trace['arguments'])
        if name=='world_sources':visits.append('catalog:'+args.get('source_type','all'))
        elif name=='world_read':visits.append('read:'+by_id.get(args['source_id'],'unknown'))
        elif name=='world_materials':visits.append('read:all_available')
        elif name.startswith('inquiry_'):visits.append(name)
    for evidence in state.get('evidence',{}).values():read.update(originals(evidence['result']))
    revisions=state.get('hypothesis_updates',[]);answer=(row['result'] or {}).get('answer') or state.get('proposed_answer') or {}
    memory=spec.get('world_memory');supplied=any(o.get('kind')=='method_memory' for o in state.get('observations',[]))
    return dict(normalized_path=visits,read_source_types=sorted({by_id[i] for i in read}),read_original_count=len(read),
                hypothesis_revisions=len(revisions),post_original_revisions=sum(bool(u.get('original_evidence_ids')) for u in revisions),
                mid_investigation_post_original_revisions=sum(bool(u.get('original_evidence_ids')) and u.get('origin')!='model_final_report' for u in revisions),
                planned_source_sequences=[u['plan']['next_sources'] for u in revisions],
                actions=sorted({r['action'] for r in answer.get('recommendations',[])}) if isinstance(answer.get('recommendations',[]),list) else [],
                stop_reason=answer.get('stop_reason'),memory_context_supplied=supplied,
                memory_admitted=len(memory['records']) if memory else 0,memory_host_rejections=memory.get('rejected',[]) if memory else [],
                memory_reviews=answer.get('method_review',[]),report_present=bool(row['result']),reason=row['reason'],
                original_timestamps_host_materialized=True)


def _summarize(rows):
    values=[r['assessment'] for r in rows];component_names=values[0]['components'] if values else []
    return dict(runs=len(rows),host_accepted=sum(v['host_accepted'] for v in values),strict_reference_passed=sum(v['strict_reference_passed'] for v in values),
        mean_quality=mean([v['quality'] for v in values]),components={k:mean([v['components'][k] for v in values]) for k in component_names},
        unsupported_claims=sum(len(v['unsupported_claim_ids']) for v in values),unsafe_typed_draft_actions=sum(bool(v['unsafe_draft_actions']) for v in values),
        false_logical_conflicts=sum(v['false_logical_conflict'] for v in values),reference_over_escalations=sum(v['reference_over_escalation'] for v in values),
        failed_or_blocked=sum(not v['host_accepted'] for v in values),mean_tool_attempts=mean([r['tool_attempts'] for r in rows]),
        mean_access_units=mean([r['access_cost_units'] for r in rows]),mean_elapsed_seconds=mean([r['elapsed_seconds'] for r in rows]),
        p95_elapsed_seconds=sorted(r['elapsed_seconds'] for r in rows)[max(0,int(.95*len(rows))-1)] if rows else None,
        budget_stops=sum('budget' in (r.get('reason') or '') or 'limit' in (r.get('reason') or '') for r in rows),
        model_requests=sum(r.get('provider_requests',0) for r in rows),
        committed_estimate_cny=str(sum((Decimal(r.get('committed_estimate_cny','0')) for r in rows),Decimal(0))),
        free_text_semantics_independently_validated=False,real_analyst_time_savings_measured=False)


def report(dataset_directory,upgrade_directory,b0_directories,output):
    dataset_directory,dataset=checked_dataset(dataset_directory);upgrade_directory=Path(upgrade_directory);output=Path(output)
    if output.exists():raise FileExistsError('New analysis output required')
    output.mkdir(parents=True);tasks={t['case_id']:t for t in dataset['tasks']};service=WorldService(upgrade_directory/'service/investigations')
    results={};behaviors={};artifacts={};phase_versions={};development=[]
    ledger_path=Path(__file__).resolve().parents[2]/'runs/llm-budget.sqlite'
    with sqlite3.connect(ledger_path.resolve().as_uri()+'?mode=ro',uri=True) as ledger:
        ledger.row_factory=sqlite3.Row
        reservations=[dict(r) for r in ledger.execute('SELECT * FROM reservations')]
        holds=[dict(r) for r in ledger.execute('SELECT * FROM budget_holds')]
        slots=[dict(r) for r in ledger.execute('SELECT * FROM world_execution_slots')]
    authorization=json.loads((upgrade_directory/'authorization.json').read_text());prefix='world-upgrade-'+authorization['id']+':'
    batch=[r for r in reservations if r['run_id'].startswith(prefix)]
    def committed(rows):return sum((Decimal(r['estimated_cny'] if r['estimated_cny'] is not None else r['reserved_cny']) for r in rows),Decimal(0))
    def held(rows):return sum((Decimal(r['amount_cny']) for r in rows),Decimal(0))
    for frozen_path in sorted((upgrade_directory/'phases').glob('*/prepared.json')):
        frozen=json.loads(frozen_path.read_text());phase_versions[frozen['phase']]=dict(code=frozen['code'],extra_code=frozen['extra_code'],group=frozen['group'])
        for entry in frozen['jobs']:
            path=frozen_path.parent/(entry['job']+'-result.json')
            if not path.exists():continue
            result=json.loads(path.read_text());artifacts[str(path.relative_to(upgrade_directory))]=sha256_file(path)
            own=[r for r in batch if r['run_id']==entry['run_id']];own_holds=[r for r in holds if r['run_id']==entry['run_id']]
            result.update(provider_requests=len(own),committed_estimate_cny=str(committed(own)+held(own_holds)),
                          unknown_cost_requests=sum(r['estimated_cny'] is None for r in own))
            if frozen['group']=='development':development.append(result);continue
            key=(entry['case_id'],entry['condition'])
            if key in results:raise ValueError('Frozen evaluation case/condition was rerun')
            results[key]=result;behaviors[key]=_behavior(service,entry)
    for directory in b0_directories:
        directory=Path(directory)
        for path in sorted(directory.glob('world-*/result.json')):
            result=json.loads(path.read_text());key=(result['case_id'],'B0')
            if key in results:raise ValueError('Duplicate B0 reference result')
            results[key]=result
    expected={(t['case_id'],c) for t in dataset['tasks'] if t['fold']!='development' for c in ('B0','B1','B2','B3')}
    expected|={(t['case_id'],c) for t in dataset['tasks'] if t['fold']=='challenge' for c in ('raw_history','stale','wrong')}
    missing=sorted(expected-set(results));groups={};paired={};counterfactual=[]
    for fold in ('frozen','challenge'):
        for condition in ('B0','B1','B2','B3','raw_history','stale','wrong'):
            rows=[r for (case,c),r in results.items() if tasks[case]['fold']==fold and c==condition]
            if rows:groups[fold+':'+condition]=_summarize(rows)
        for left,right in [('B1','B0'),('B2','B1'),('B3','B2'),('B3','B1')]:
            deltas=[(t['family'],results[(case,left)]['assessment']['quality']-results[(case,right)]['assessment']['quality']) for case,t in tasks.items()
                    if t['fold']==fold and (case,left) in results and (case,right) in results]
            paired[fold+':'+left+'-'+right]=dict(pairs=len(deltas),mean_delta_quality=mean([d for _,d in deltas]),family_cluster_interval=family_bootstrap(deltas))
    pairs=defaultdict(list)
    for task in tasks.values():pairs[task['pair_id']].append(task['case_id'])
    for identity,cases in pairs.items():
        if len(cases)!=2:continue
        for condition in ('B2','B3'):
            keys=[(c,condition) for c in sorted(cases)]
            if not all(k in behaviors for k in keys):continue
            a,b=[behaviors[k] for k in keys]
            counterfactual.append(dict(pair_group=identity,case_ids=sorted(cases),condition=condition,
                normalized_source_path_changed=a['normalized_path']!=b['normalized_path'],
                planned_source_sequence_changed=a['planned_source_sequences']!=b['planned_source_sequences'],
                recommendations_changed=a['actions']!=b['actions'],
                both_reference_passed=all(results[k]['assessment']['strict_reference_passed'] for k in keys),
                both_post_original_revision=all(behaviors[k]['post_original_revisions']>0 for k in keys),
                both_mid_investigation_revision=all(behaviors[k]['mid_investigation_post_original_revisions']>0 for k in keys),
                final_hypothesis_capture_is_distinct_from_a_tool_requested_revision=True,
                caution='A changed path alone is not necessarily a rational improvement'))
    memory={}
    challenge=[case for case,t in tasks.items() if t['fold']=='challenge']
    for condition in ('B3','raw_history','stale','wrong'):
        keys=[(case,condition) for case in challenge if (case,condition) in behaviors]
        pairs=[case for case,_ in keys if (case,'B2') in results]
        memory[condition]=dict(assigned=len(keys),context_supplied=sum(behaviors[k]['memory_context_supplied'] for k in keys),
             cases_with_admitted_notes=sum(behaviors[k]['memory_admitted']>0 for k in keys),
             host_rejected_notes=sum(len(behaviors[k]['memory_host_rejections']) for k in keys),
             explicit_model_use_reports=sum(any(m.get('decision')=='use' for m in behaviors[k]['memory_reviews']) for k in keys),
             explicit_model_reject_reports=sum(any(m.get('decision')=='reject' for m in behaviors[k]['memory_reviews']) for k in keys),
             paired_with_no_memory=len(pairs),
             reference_strict_gains=sum(not results[(case,'B2')]['assessment']['strict_reference_passed'] and results[(case,condition)]['assessment']['strict_reference_passed'] for case in pairs),
             reference_strict_losses=sum(results[(case,'B2')]['assessment']['strict_reference_passed'] and not results[(case,condition)]['assessment']['strict_reference_passed'] for case in pairs),
             new_observed_fact_errors=sum(results[(case,condition)]['assessment']['supported_claim_count']<results[(case,condition)]['assessment']['claim_count'] and
                 results[(case,'B2')]['assessment']['supported_claim_count']==results[(case,'B2')]['assessment']['claim_count'] for case in pairs),
             mean_delta_quality=mean([results[(case,condition)]['assessment']['quality']-results[(case,'B2')]['assessment']['quality'] for case in pairs]),
             host_ttl_rejection_is_not_model_reasoning=True,single_model_draw_per_case_condition=True)
    amendment_path=upgrade_directory/'budget-amendment.json'
    amendment=json.loads(amendment_path.read_text()) if amendment_path.exists() else None
    budget=dict(scope_id=authorization['id'],max_upgrade_cny=amendment['max_estimated_cny'] if amendment else '8',max_project_cny='25',provider_requests=len(batch),
        registered_case_slots=sum(s['authorization_id']==authorization['id'] for s in slots),
        known_usage_estimate_cny=str(sum((Decimal(r['estimated_cny']) for r in batch if r['estimated_cny'] is not None),Decimal(0))),
        unknown_reservation_cny=str(sum((Decimal(r['reserved_cny']) for r in batch if r['status']=='uncertain_reserved'),Decimal(0))),
        in_flight_reservation_cny=str(sum((Decimal(r['reserved_cny']) for r in batch if r['status']=='reserved'),Decimal(0))),
        additional_risk_hold_cny=str(held([r for r in holds if r['run_id'].startswith(prefix)])),
        upgrade_committed_estimate_cny=str(committed(batch)+held([r for r in holds if r['run_id'].startswith(prefix)])),
        project_committed_estimate_cny=str(committed(reservations)+held(holds)),
        unknown_provider_charges=sum(r['status']=='uncertain_reserved' for r in batch),in_flight_requests=sum(r['status']=='reserved' for r in batch),provider_bill_verified=False)
    body=dict(status='complete_internal_simulation_comparison' if not missing else 'incomplete_do_not_claim_full_acceptance',
        missing_case_conditions=missing,groups=groups,paired_quality=paired,counterfactual_pairs=counterfactual,
        behaviors={case+':'+condition:b for (case,condition),b in behaviors.items()},
        development=dict(attempted_or_terminal_entries=len(development),conditions=Counter(r['condition'] for r in development)),
        phase_versions=phase_versions,artifact_sha256=artifacts,memory_comparisons=memory,budget=budget,author_reference_not_external_expert=True,
        real_business_benefit_validated=False,default_strategy_recommendation='pending_complete_safety_quality_cost_comparison')
    if not missing:
        folds=('frozen','challenge')
        code_close=all(groups[f+':B0']['mean_quality']>=max(groups[f+':'+c]['mean_quality'] for c in ('B0','B1','B2','B3'))-.02 and
            groups[f+':B0']['strict_reference_passed']/groups[f+':B0']['runs']>=max(groups[f+':'+c]['strict_reference_passed']/groups[f+':'+c]['runs'] for c in ('B0','B1','B2','B3'))-.05 and
            groups[f+':B0']['unsafe_typed_draft_actions']==0 for f in folds)
        def gain(left,right):
            return all(paired[f+':'+left+'-'+right]['mean_delta_quality']>=.05 and
              paired[f+':'+left+'-'+right]['family_cluster_interval']['low']>0 and
              groups[f+':'+left]['unsupported_claims']<=groups[f+':'+right]['unsupported_claims'] and
              groups[f+':'+left]['unsafe_typed_draft_actions']<=groups[f+':'+right]['unsafe_typed_draft_actions'] for f in folds)
        body['default_strategy_recommendation']=('B0_deterministic' if code_close else 'B3_adaptive_reviewed_methods' if gain('B3','B1') and gain('B3','B2')
                                                  else 'B2_adaptive_no_memory' if gain('B2','B1') else 'B1_all_materials')
        body['strategy_choice_scope']='Finite internal simulation only; no claim of external equivalence, analyst savings or automatic case closure'
    write_json(output/'analysis.json',body);return body
