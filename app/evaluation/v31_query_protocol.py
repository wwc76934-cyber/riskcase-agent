"""Controller-only data audit, paired schedule and offline deterministic baseline."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from random import Random

from app.evaluation.v3_tasks import write_new
from app.evaluation.v31_query_score import REFERENCE, score_stage
from app.investigation.contracts import digest
from app.investigation.v3.query_world import SourceQueryWorld
from app.investigation.v3.trajectory import run_case
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess


CONDITIONS = ('B0','B1','B4','B2')
REPEATS = 3
SEED = 20261104


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_case(snapshot, reference):
    world = SourceQueryWorld(snapshot)
    stages, witness = reference['stages'], reference['offline_reachability_witness']
    if reference['case_id'] != snapshot['case_id'] or reference['schema'] != REFERENCE or len(stages) != 3 or len(witness) != 3:
        raise ValueError('Mismatched case, reference or stage count')
    target = snapshot['alert']['transaction_id']
    for i, (stage, batches) in enumerate(zip(stages, witness)):
        if i:world.advance(i)
        visible = set(world.eligible())
        if stage['stage'] != i or set(stage['visible_sources']) != visible:
            raise ValueError('Reference visibility differs from the source world')
        for sources in batches:world.read(sources)
        known = set(world.acquired)
        branches = stage['conditional_state']
        if not branches or branches[-1]['requires_originals'] or branches[-1]['values'] != ['unresolved']:
            raise ValueError('A final unresolved branch is required')
        expected = {(o['source_id'],o['predicate']) for o in stage['observations']}
        if len(expected) != len(stage['observations']) or not {s for s,_ in expected} <= visible:
            raise ValueError('Duplicate or invisible reference observation')
        if any(target not in world.records[s]['transaction_ids'] for s,_ in expected):
            raise ValueError('Reference observation targets another transaction')
        for branch in [stage['full_state'],*branches]:
            if not set(branch['sources']+branch['requires_originals']) <= visible:
                raise ValueError('State reference cites a future or hidden source')
        if not set(stage['full_state']['sources'] + stage['required_counter_sources']) <= known:
            raise ValueError('Reachability witness omitted source statement or counterevidence')
        if any(tuple(fact) not in expected or fact[0] not in known for fact in stage['required_facts']):
            raise ValueError('Required observation lacks an acquired original or a reference value')
        for alternatives in stage['critical_evidence']:
            if not alternatives or any(item['source_id'] not in visible for item in alternatives):
                raise ValueError('Invalid critical evidence group')
            if not any(world.queried.get(item['source_id'],{}).get('status') == item['status'] for item in alternatives):
                raise ValueError('Reference asks for evidence unreachable in the witness path')
        if any(not group or not set(group) <= set(stage['allowed_actions']) for group in stage['required_action_groups']):
            raise ValueError('Inconsistent action reference')
    return dict(witness_cost_units=world.spent, witness_elapsed_minutes=world.elapsed,
        simulated_unavailable_queries=sum(r['status']=='unavailable' for r in world.queried.values()),
        references_opened_by_model=False)


def read_dataset(directory):
    directory = Path(directory)
    manifest_file = directory/'dataset.json'
    fingerprint = sha(manifest_file)
    if fingerprint != load(directory/'dataset-sha256.json')['sha256']:
        raise ValueError('Candidate manifest changed')
    manifest = load(manifest_file)
    entries = manifest['cases']
    if len(entries) != 40 or manifest['review_scope'] != 'AI_author_reviewed_simulation' or manifest['independent_person_reviewed']:
        raise ValueError('Declared 40-case author-simulation scope required')
    for key in ('case_id','subject_id','template_id'):
        if len({e[key] for e in entries}) != len(entries):
            raise ValueError('Duplicate '+key)
    for entry in entries:
        # Construct paths from the case ID, with no controller path in runtime.
        identity=entry['case_id']
        if not identity or not all(c.isascii() and (c.isalnum() or c in '-_') for c in identity):
            raise ValueError('Invalid case ID')
        runtime=f'runtime/{identity}.json'; control=f'controller/{identity}.json'
        if entry['runtime']!=runtime or entry['reference']!=control:
            raise ValueError('Unregistered case artifact path')
        snapshot,reference=load(directory/runtime),load(directory/control)
        if digest(snapshot)!=entry['runtime_digest'] or digest(reference)!=entry['reference_digest']:
            raise ValueError('Candidate content changed')
        if (snapshot['case_id'],snapshot['subject_id'],reference['template_id'],reference['family'])!=(
            identity,entry['subject_id'],entry['template_id'],entry['family']):
            raise ValueError('Candidate identity mismatch')
        yield fingerprint,entry,snapshot,reference


def prepare(directory, output):
    cases=list(read_dataset(directory))
    audits={entry['case_id']:audit_case(snapshot,reference) for _,entry,snapshot,reference in cases}
    dataset_fingerprint=cases[0][0]
    schedule=[dict(case_id=entry['case_id'],condition=condition,repeat=repeat)
              for _,entry,_,_ in cases for condition in CONDITIONS for repeat in range(1,REPEATS+1)]
    Random(SEED).shuffle(schedule)
    protocol=dict(schema='v31-query-offline-preregistration-v1',dataset_sha256=dataset_fingerprint,
        cases=[dict(case_id=e['case_id'],family=e['family']) for _,e,_,_ in cases],
        conditions=list(CONDITIONS),repeats=REPEATS,seed=SEED,bootstrap_replicates=2000,
        contrasts=[['B4','B0'],['B2','B1'],['B2','B4']],
        primary_metric='strict_trajectory', planned_case_runs=len(schedule), planned_stages=len(schedule)*3,
        deterministic_B0_reuse=True, max_per_stage_requests=dict(B0=0,B1=3,B4=2,B2=6),
        scoring_sha256=sha(Path(__file__).with_name('v31_query_score.py')),schedule_digest=digest(schedule),
        author_reviewed_simulation=True, independent_human_review=False, gate3_ready=False,
        external_requests_authorized=False, memory_condition_enabled=False)
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    write_new(output/'protocol.json',protocol)
    write_new(output/'schedule.json',schedule)
    write_new(output/'reachability-audit.json',audits)
    return protocol


def offline_baseline(directory, output):
    cases=list(read_dataset(directory))
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    store=JobStore(output/'baseline.sqlite')
    rows=[]; summary=Counter(); per_family={}
    for _,entry,snapshot,reference in cases:
        case_id=entry['case_id'];family=entry['family']
        store.submit(case_id,dict(mode='offline',case_id=case_id,question=dict(text='Simulated evidence review')))
        claim=store.claim('v31-offline-B0',lease_seconds=120,deadline_seconds=600,mode='offline')
        source=SourceAccess(store.path,claim,snapshot)
        result=run_case(source,'B0')
        store.finish(claim['id'],claim['owner'],claim['generation'],'awaiting_review',result=result)
        phases=[dict(stage=p['stage'], assessment=score_stage(snapshot,reference,p['stage'],p['evidence'],p['report']))
                for p in result['phases']]
        family_counts=per_family.setdefault(family,Counter())
        for counts in (summary,family_counts):
            counts['cases']+=1
            counts['stages']+=3
            counts['delivered']+=sum(p['assessment']['delivered'] for p in phases)
            counts['strict_stages']+=sum(p['assessment']['strict_author_reference_pass'] for p in phases)
            counts['strict_cases']+=int(len(phases)==3 and all(p['assessment']['strict_author_reference_pass'] for p in phases))
            counts['target_state_correct']+=sum(p['assessment']['full_information_target_correct'] for p in phases)
            counts['critical_groups']+=sum(p['assessment']['critical_evidence_groups'] for p in phases)
            counts['critical_groups_acquired']+=sum(p['assessment']['acquired_critical_evidence_groups'] for p in phases)
        rows.append(dict(case_id=case_id,family=family,phases=phases,model_requests=0,
                         tool_attempts=sum(p['source_tool_attempts'] for p in result['phases'])))
    write_new(output/'B0.json',rows)
    receipt=dict(B0=summary,by_family=per_family,external_model_requests=0,
                 same_controller_scoring=True,formal_value_claim=False)
    write_new(output/'summary.json',receipt)
    return receipt
