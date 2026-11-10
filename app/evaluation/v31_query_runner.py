"""Offline paired study execution. Paid execution requires a separate controller."""
from collections import Counter
from decimal import Decimal
import hashlib
from pathlib import Path

from app.evaluation.v3_paired import paired_family_interval
from app.evaluation.v3_tasks import write_new
from app.evaluation.v31_query_protocol import CONDITIONS, REPEATS, load, read_dataset, sha
from app.evaluation.v31_query_score import score_stage
from app.investigation.contracts import digest
from app.investigation.v3.mcp_trajectory import run_case_mcp
from app.investigation.v3.study_delivery import StudyHybridReviewContract, StudyReviewContract
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess


def registered_plan(dataset, preregistration):
    preregistration=Path(preregistration)
    cases=list(read_dataset(dataset))
    protocol=load(preregistration/'protocol.json')
    schedule=load(preregistration/'schedule.json')
    scorer=Path(__file__).with_name('v31_query_score.py').read_bytes().replace(b'\r\n',b'\n')
    scorer_fingerprints={hashlib.sha256(data).hexdigest() for data in (scorer,scorer.replace(b'\n',b'\r\n'))}
    expected_cases=[dict(case_id=entry['case_id'],family=entry['family']) for _,entry,_,_ in cases]
    if (protocol['schema']!='v31-query-offline-preregistration-v1' or protocol['cases']!=expected_cases
        or protocol['dataset_sha256']!=cases[0][0] or protocol['conditions']!=list(CONDITIONS)
        or protocol['repeats']!=REPEATS or protocol['external_requests_authorized'] is not False
        or protocol['memory_condition_enabled'] is not False
        or protocol['scoring_sha256'] not in scorer_fingerprints
        or protocol['schedule_digest']!=digest(schedule)):
        raise ValueError('Offline preregistration changed')
    registered={(row['case_id'],row['condition'],row['repeat']) for row in schedule}
    full={(case['case_id'],condition,repeat) for case in expected_cases for condition in CONDITIONS
          for repeat in range(1,REPEATS+1)}
    if registered!=full or len(schedule)!=len(full):
        raise ValueError('Schedule is not the declared complete paired study')
    protocol['protocol_sha256']=sha(preregistration/'protocol.json')
    return protocol,schedule,{entry['case_id']:(entry,snapshot,reference) for _,entry,snapshot,reference in cases}


def assert_scripted(client):
    if getattr(client,'simulation',False) is not True:
        raise ValueError('Offline controller accepts scripted clients only')
    return client


async def run_offline_case(dataset, preregistration, output, case_id, *, scripted_client_factory):
    """Exercise all four groups through MCP on one case without any provider client."""
    protocol,_,cases=registered_plan(dataset,preregistration)
    entry,snapshot,_=cases[case_id]
    # Do not inject the controller packet, reference or scoring object into any client.
    runtime_path=Path(dataset)/entry['runtime']
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    rows=[]
    for condition in CONDITIONS:
        condition_dir=output/condition;condition_dir.mkdir()
        store=JobStore(condition_dir/'job.sqlite')
        store.submit(case_id,dict(mode='offline',case_id=case_id,question=dict(text='Author simulation protocol check')))
        claim=store.claim('offline-v31-study',lease_seconds=120,deadline_seconds=600,mode='offline')
        source=SourceAccess(store.path,claim,snapshot,max_tools=40)
        if condition=='B0':
            factory=contract=None
        else:
            factory=lambda stage:assert_scripted(scripted_client_factory(condition,stage))
            contract=StudyHybridReviewContract if condition=='B4' else StudyReviewContract
        with (condition_dir/'mcp.log').open('x',encoding='utf-8') as error_log:
            result=await run_case_mcp(source,runtime_path,condition,factory,errlog=error_log,report_contract_factory=contract)
        store.finish(claim['id'],claim['owner'],claim['generation'],'awaiting_review',result=result)
        reference=load(Path(dataset)/entry['reference'])
        if digest(reference)!=entry['reference_digest']:
            raise ValueError('Reference changed after execution')
        phases=[]
        for phase in result['phases']:
            stages=dict(stage=phase['stage'], source_tool_attempts=phase['source_tool_attempts'],
                        elapsed_seconds=phase['process_elapsed_seconds_including_recovery'],
                        simulated_query_minutes=phase['simulated_source_latency_minutes'],
                        assessment=score_stage(snapshot,reference,phase['stage'],phase['evidence'],phase['report']))
            phases.append(stages)
        row=dict(case_id=case_id,condition=condition,repeat=1,
            dataset_sha256=protocol['dataset_sha256'],protocol_sha256=protocol['protocol_sha256'],
            evidence_kind='scripted_protocol_probe',phases=phases,
            model_requests=sum(p['protocol_request_attempts'] for p in result['phases']),
            external_model_requests=0,estimated_cny='0',risk_hold_cny='0',source_transport=result['source_transport'])
        write_new(condition_dir/'result.json',row)
        rows.append(row)
    return rows


def summarize(dataset, preregistration, rows):
    rows = list(rows)
    if any(row["evidence_kind"] != "scripted_protocol_probe" or row["external_model_requests"] != 0 for row in rows):
        raise ValueError("Offline controller cannot combine live evidence")
    return summarize_registered(dataset, preregistration, rows, scope="offline_protocol_check_only")


def summarize_registered(dataset, preregistration, rows, *, scope):
    protocol,schedule,cases=registered_plan(dataset,preregistration)
    rows_by_slot={}
    for row in rows:
        slot=row['case_id'],row['condition'],row['repeat']
        if slot in rows_by_slot:
            raise ValueError('Duplicate row')
        if row['condition']=='B0' and row['repeat']!=1:
            raise ValueError('B0 is one deterministic run per case')
        rows_by_slot[slot]=row
    if set(rows_by_slot)-{(x['case_id'],x['condition'],x['repeat']) for x in schedule}:
        raise ValueError('Unknown condition slot')
    for identity,condition,repeat in list(rows_by_slot):
        if condition=='B0':
            for reuse in range(2,REPEATS+1):
                rows_by_slot[(identity,condition,reuse)]=dict(rows_by_slot[(identity,condition,repeat)],
                    repeat=reuse,deterministic_result_reused=True)
    # The paired engine independently validates fingerprints, stages and metrics.
    contrasts=[paired_family_interval(protocol,list(rows_by_slot.values()),a,b,
        replicates=protocol['bootstrap_replicates'],seed=protocol['seed']) for a,b in protocol['contrasts']]
    by_condition={}
    for condition in CONDITIONS:
        slots=[(slot['case_id'],condition,slot['repeat']) for slot in schedule if slot['condition']==condition]
        counts=Counter(dict(recorded_cases=0,recorded_stages=0,delivered_stages=0,strict_stages=0,
                            complete_delivery_cases=0,strict_cases=0,requests=0,critical_groups_acquired=0))
        cost=Decimal(0)
        for slot in slots:
            _,_,reference=cases[slot[0]]
            counts['planned_critical_groups']+=sum(len(stage['critical_evidence']) for stage in reference['stages'])
            if slot not in rows_by_slot:continue
            row=rows_by_slot[slot]
            counts['recorded_cases']+=1
            counts['requests']+=row['model_requests']
            cost+=Decimal(row['estimated_cny'])+Decimal(row['risk_hold_cny'])
            counts['strict_cases']+=int(len(row['phases'])==3 and all(
                p['assessment']['strict_author_reference_pass'] for p in row['phases']))
            counts['complete_delivery_cases']+=int(len(row['phases'])==3 and all(
                p['assessment']['delivered'] for p in row['phases']))
            for phase in row['phases']:
                assessment=phase['assessment']
                counts['recorded_stages']+=1
                counts['delivered_stages']+=assessment['delivered']
                counts['strict_stages']+=assessment['strict_author_reference_pass']
                counts['target_state_correct']+=assessment['full_information_target_correct']
                counts['target_state_correct_given_acquired']+=assessment['reasoning_correct_given_acquired_sources']
                counts['critical_groups_acquired']+=assessment['acquired_critical_evidence_groups']
                counts['source_tool_attempts']+=phase['source_tool_attempts']
                counts['observed_elapsed_seconds']+=phase['elapsed_seconds']
                counts['simulated_query_minutes']+=phase['simulated_query_minutes']
                counts['incorrect_safe_close']+=assessment['incorrect_safe_close']
                counts['outside_reference_escalation']+=assessment['outside_reference_escalation']
                for key in ('fact_count','covered_correct_fact_count','covered_contradicted_fact_count',
                            'unscored_supported_fact_count','supported_fact_count'):
                    counts[key]+=assessment[key]
        covered=counts['covered_correct_fact_count']+counts['covered_contradicted_fact_count']
        by_condition[condition]=dict(counts,planned_cases=len(slots),planned_stages=3*len(slots),
            fact_accuracy_among_covered=counts['covered_correct_fact_count']/covered if covered else None,
            evidence_support=counts['supported_fact_count']/counts['fact_count'] if counts['fact_count'] else None,
            strict_given_delivery=counts['strict_stages']/counts['delivered_stages'] if counts['delivered_stages'] else None,
            independent_cases=len(cases),known_committed_estimate_cny=str(cost))
    return dict(scope=scope,conditions=by_condition,paired_contrasts=contrasts,
                external_model_requests=sum(row['external_model_requests'] for row in rows),formal_agent_value_claim=False)
