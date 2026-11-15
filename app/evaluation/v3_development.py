"""Free development B0 trajectories; no provider, key lookup or held-out scoring."""
from pathlib import Path
import json,hashlib,time
from app.service.store import JobStore
from app.service.v3_sources import SourceAccess
from app.investigation.v3.rule_workflow import collect_rule_workflow,rule_report
from app.evaluation.v3_score import score_stage


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_b0(dataset,output,*,fold='development'):
    if fold!='development':raise ValueError('Held-out candidates require actual independent review and a formal frozen protocol')
    dataset,output=Path(dataset),Path(output)
    if output.exists():raise FileExistsError('Preserve prior development results')
    manifest=load(dataset/'dataset.json')
    if sha(dataset/'dataset.json')!=load(dataset/'dataset-sha256.json')['sha256']:raise ValueError('Dataset changed')
    output.mkdir(parents=True);(output/'cases').mkdir();store=JobStore(output/'jobs.sqlite');results=[]
    for entry in manifest['entries']:
        if entry['fold']!='development':continue
        runtime_path=dataset/entry['runtime'];reference_path=dataset/entry['reference']
        if runtime_path.resolve().parent!=(dataset/'runtime').resolve() or reference_path.resolve().parent!=(dataset/'controller').resolve():raise ValueError('Artifact path scope')
        if sha(runtime_path)!=entry['runtime_sha256'] or sha(reference_path)!=entry['reference_sha256']:raise ValueError('Case artifact changed')
        runtime,reference=load(runtime_path),load(reference_path)
        if reference['fold']!='development' or reference['case_id']!=runtime['case_id']:raise ValueError('Reference scope')
        identity,_=store.submit(entry['case_id']+'-B0',dict(mode='offline',case_id=entry['case_id'],question=dict(text='Development B0 trajectory')))
        claim=store.claim('b0-development',lease_seconds=120,deadline_seconds=600,mode='offline');source=SourceAccess(store.path,claim,runtime);phases=[]
        for stage in range(3):
            if stage:source.advance(stage)
            started=time.monotonic();report=None;error=None
            try:report=rule_report(collect_rule_workflow(source))
            except Exception as exc:error=dict(type=type(exc).__name__,code=getattr(exc,'code',None))
            assessment=score_stage(runtime,reference,stage,source.evidence(),report)
            phases.append(dict(stage=stage,error=error,elapsed_seconds=round(time.monotonic()-started,6),report=report,
                assessment=assessment,source_state=source.position(),model_requests=0,model_selected_tool_calls=0))
        row=dict(case_id=entry['case_id'],family=entry['family'],condition='B0',development_only=True,phases=phases,
            trajectory_strict=all(p['assessment']['strict_author_reference_pass'] for p in phases),T2_target_correct=phases[2]['assessment']['full_information_target_correct'])
        store.finish(identity,claim['owner'],claim['generation'],'awaiting_review',result=row)
        (output/'cases'/(entry['case_id']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8');results.append(row)
    source_root=Path(__file__).resolve().parents[2]
    summary=dict(schema='v3-b0-development-trajectories-v1',dataset_sha256=sha(dataset/'dataset.json'),cases=len(results),
        code_sha256={str(p.relative_to(source_root)):sha(p) for directory in ('app/investigation/v3','app/service','app/evaluation') for p in sorted((source_root/directory).glob('*.py'))},
        delivered_stages=sum(p['assessment']['delivered'] for r in results for p in r['phases']),
        strict_stages=sum(p['assessment']['strict_author_reference_pass'] for r in results for p in r['phases']),
        strict_trajectories=sum(r['trajectory_strict'] for r in results),T2_target_correct=sum(r['T2_target_correct'] for r in results),
        stage_denominator=len(results)*3,trajectory_denominator=len(results),external_model_requests=0,
        evaluation_candidates_scored=0,author_reference_not_expert=True,development_only=True,
        formal_comparison=False,agent_value_claim=False,case_results_sha256={p.name:sha(p) for p in (output/'cases').glob('*.json')})
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary
