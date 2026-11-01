"""Independent dataset audit: SQL source equality, time and split separation."""
from collections import Counter,defaultdict
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from app.investigation.contracts import digest
from app.risk.case_snapshot import COLUMNS
from app.risk.history import sha256_file


def audit(directory,cohort_report):
    directory,cohort_report=Path(directory),Path(cohort_report)
    dataset=json.loads((directory/'dataset.json').read_text())
    from app.investigation.fraud_world import World
    if dataset.get('generation_source_archived'):
        for name,expected in dataset['generation_code_sha256'].items():
            if sha256_file(directory/'controller/generation-source'/name)!=expected:raise ValueError('Generation source snapshot changed')
        if sha256_file(directory/'controller/generation-source/config/fraud-world-v1.json')!=dataset['protocol_sha256']:
            raise ValueError('Data protocol snapshot changed')
    facts=cohort_report.parent/'dataset/facts.sqlite';provenance=dataset['source_provenance']
    if sha256_file(cohort_report)!=provenance['cohort_report_sha256'] or sha256_file(facts)!=provenance['source_facts_sha256']:
        raise ValueError('Source changed')
    for name,expected in dataset['artifact_sha256'].items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256_file(path)!=expected:raise ValueError('Dataset artifact changed')
    families=defaultdict(set);subjects=defaultdict(set);targets=defaultdict(set);pairs=defaultdict(list)
    totals=Counter();sizes=[];cases=[]
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        for task in dataset['tasks']:
            runtime=json.loads((directory/task['runtime']).read_text());truth=json.loads((directory/task['evaluation']).read_text())
            a=runtime['transaction_snapshot'];cutoff=a['cutoff'];as_of=datetime.fromisoformat(runtime['investigation_as_of'])
            original=[dict(r) for r in db.execute('SELECT '+','.join(COLUMNS)+' FROM transactions WHERE user_id=? AND event_time>=? AND event_time<? ORDER BY event_time,source_row',(task['subject'],a['history_start'],cutoff))]
            target=dict(db.execute('SELECT '+','.join(COLUMNS)+' FROM transactions WHERE transaction_id=?',(a['target_id'],)).fetchone())
            if original+[target]!=a['rows'] or a['rows_sha256']!=digest(a['rows']):raise ValueError('A projection does not match original source SQL')
            if (target['user_id'],target['event_time'])!=(task['subject'],cutoff):raise ValueError('A target identity mismatch')
            if digest(runtime)!=truth['runtime_sha256']:raise ValueError('C bound to wrong runtime')
            eligible={r['source_id'] for r in runtime['records'] if datetime.fromisoformat(r['event_time'])<=as_of and datetime.fromisoformat(r['available_at'])<=as_of}
            if eligible!=set(truth['eligible_source_ids']):raise ValueError('Availability reference mismatch')
            for claim in truth['expected_claims']:
                record=next(r for r in runtime['records'] if r['source_id']==claim['source_id'])
                if claim['source_id'] not in eligible or not 1<=claim['line']<=len(record['text'].splitlines()):raise ValueError('C cites unavailable/nonexistent evidence')
            if set(runtime)&{'family','fold','expected_action','expected_claims','evaluation'}:raise ValueError('C fields leaked')
            world=World(runtime)
            packet=world.call('world_materials',{})
            packet_bytes=len(json.dumps(packet,ensure_ascii=False).encode())
            sizes.append(packet_bytes)
            if packet_bytes>180000:raise ValueError('All-material baseline packet exceeds shared result limit')
            if not set(truth.get('required_predicates',[]))<={c['predicate'] for c in truth['expected_claims']}:
                raise ValueError('Required claim has no available original')
            fold=task['fold'];families[fold].add(task['family']);subjects[fold].add(task['subject']);targets[fold].add(task['target_id'])
            pairs[task['pair_id']].append((task,digest(a['rows']),a['alert']))
            totals[fold]+=1
            cases.append(dict(case_id=task['case_id'],A_rows=len(a['rows']),B_records=len(runtime['records']),future_excluded=len(runtime['records'])-len(eligible)))
    folds=list(totals)
    for i,first in enumerate(folds):
        for second in folds[i+1:]:
            if families[first]&families[second] or subjects[first]&subjects[second] or targets[first]&targets[second]:
                raise ValueError('Family/subject/target crosses folds')
    if dict(totals)!={'development':30,'frozen':40,'challenge':20}:raise ValueError('90-case split mismatch')
    paired=0
    for values in pairs.values():
        if len(values)>2 or len({v[0]['fold'] for v in values})!=1 or len({v[1] for v in values})!=1 or any(v[2]!=values[0][2] for v in values):
            raise ValueError('Counterfactual pair changed its initial A facts')
        paired+=len(values)==2
    return dict(status='source_equality_time_and_split_verified_NOT_model_results',counts=dict(totals),
                case_count=sum(totals.values()),scenario_families=sum(map(len,families.values())),
                distinct_subjects=sum(map(len,subjects.values())),paired_A_identical_groups=paired,
                artifacts_verified=len(dataset['artifact_sha256']),source_SQL_recomputed=True,
                all_material_packet_bytes=dict(min=min(sizes),max=max(sizes)),
                cases=cases,real_model_calls=0,expert_review=False,truth_semantics_independently_reviewed=False)
