"""Counterfactual metadata repair on saved proposals. Never rewrites old scores."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
from jsonschema import ValidationError
from app.agent.structured_output import parse_model_json
from app.evaluation.v3_failure_audit import read,sha
from app.investigation.fraud_world import materialize_timeline,verify
from app.investigation.contracts import digest
from app.investigation.v3.offline_replay import rebind_saved_timeline


def replay(upgrade,output):
    upgrade,output=Path(upgrade),Path(output)
    if output.exists():raise FileExistsError('New replay directory required')
    source=upgrade/'service/investigations/jobs.sqlite';before=sha(source);rows=[];hashes={}
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for phase in ('main-01','main-finance-02'):
            directory=upgrade/'phases'/phase
            for e in read(directory/'prepared.json')['jobs']:
                path=directory/(e['job']+'-result.json')
                if not path.exists():continue
                old=read(path);hashes[str(path.relative_to(upgrade))]=sha(path)
                stored=db.execute('SELECT body FROM investigation_state WHERE job_id=?',(e['job'],)).fetchone()
                state=json.loads(stored[0]);answer=state.get('proposed_answer')
                if answer is None:
                    try:answer,_=parse_model_json(state.get('raw',{}).get('content') or '')
                    except (ValueError,TypeError):pass
                memories={r['memory_id'] for o in state.get('observations',[]) if o.get('kind')=='method_memory' for r in o['records']}
                row=dict(case_id=e['case_id'],condition=e['condition'],original_host_accepted=old['assessment']['host_accepted'],
                         saved_proposal_present=isinstance(answer,dict),before_structural_pass=False,after_structural_pass=False,
                         original_result_sha256=sha(path),rebound_container_count=0)
                if isinstance(answer,dict):
                    try:
                        hydrated,_=materialize_timeline(answer,state['evidence']);verify(hydrated,state['evidence'],memory_ids=memories)
                        row['before_structural_pass']=True
                    except (ValueError,KeyError,TypeError,ValidationError) as exc:row['before_error']=type(exc).__name__
                    try:
                        corrected,changes=rebind_saved_timeline(answer,state['evidence'])
                        verify(corrected,state['evidence'],memory_ids=memories)
                        row['after_structural_pass']=True;row['rebound_container_count']=sum(c['operation']=='rebind_acquired_original_container' for c in changes)
                        row['derived_answer_sha256']=digest(corrected)
                    except (ValueError,KeyError,TypeError,ValidationError) as exc:row['after_error']=type(exc).__name__
                rows.append(row)
    if len(rows)!=180 or sha(source)!=before or any(sha(upgrade/p)!=h for p,h in hashes.items()):raise ValueError('Historical scope or inputs changed')
    summary=dict(schema='v3-offline-metadata-replay-v1',conditions=180,
        original_delivered=sum(r['original_host_accepted'] for r in rows),
        before_structural_pass=sum(r['before_structural_pass'] for r in rows),
        after_structural_pass=sum(r['after_structural_pass'] for r in rows),
        repaired_structure_only=sum(not r['before_structural_pass'] and r['after_structural_pass'] for r in rows),
        no_longer_structurally_valid=sum(r['before_structural_pass'] and not r['after_structural_pass'] for r in rows),
        rows=rows,original_db_sha256=before,original_inputs_unchanged=True,
        external_model_requests=0,new_model_quality_claim=False,
        limits=['Only existing acquired event container references may be rebound',
                'No unread source, new claim, fact value, recommendation or citation quote is added',
                'Not a model rerun, product delivery rate, end-to-end recovery or new business score',
                'Changes were designed after inspecting these cases; this is observed-case regression'])
    output.mkdir(parents=True);(output/'replay.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary
