"""Controller for frozen source/evidence evaluation, never imported by tools."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time

from app.investigation.contracts import digest
from app.investigation.fraud_world import World,verify
from app.investigation.gateway import connect,Gateway
from app.investigation.world_baseline import report_from_observations
from app.risk.history import sha256_file
from app.service.runtime import write_json
from app.evaluation.world_score import score

ROOT=Path(__file__).resolve().parents[2]


def checked_dataset(directory):
    directory=Path(directory).resolve();dataset=json.loads((directory/'dataset.json').read_text())
    for name,expected in dataset['artifact_sha256'].items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory) or sha256_file(path)!=expected:raise ValueError('Frozen dataset changed')
    return directory,dataset


async def collect_b0(runtime,directory):
    path=directory/'snapshot.json';write_json(path,runtime)
    with (directory/'mcp-stderr.log').open('w',encoding='utf-8') as errors:
        async with connect(path,runtime,errlog=errors) as (sessions,handshakes):
            gateway=Gateway(sessions,runtime,max_attempts=12)
            observations=[]
            for name in ('inquiry_overview','world_materials'):
                observation=await gateway.call(name,'{}');observations.append(observation)
                if not observation['ok']:raise ValueError('B0 source access failed: '+observation.get('error','unknown'))
            write_json(directory/'mcp-handshakes.json',handshakes)
            write_json(directory/'evidence.json',gateway.evidence);write_json(directory/'trace.json',gateway.trace)
            answer=report_from_observations(observations)
            validation=verify(answer,gateway.evidence)
            return answer,validation,gateway.trace


def run_b0(dataset_directory,output,fold='development'):
    dataset_directory,dataset=checked_dataset(dataset_directory);output=Path(output)
    if output.exists():raise FileExistsError('New B0 output required')
    output.mkdir(parents=True)
    tasks=[t for t in dataset['tasks'] if t['fold']==fold]
    if not tasks:raise ValueError('Unknown data fold')
    frozen=dict(dataset_sha256=sha256_file(dataset_directory/'dataset.json'),fold=fold,case_ids=[t['case_id'] for t in tasks],
                code_sha256={name:sha256_file(ROOT/name) for name in ('app/investigation/world_baseline.py','app/evaluation/world_score.py','app/evaluation/world_runner.py','config/fraud-world-scoring-v1.json')})
    from app.investigation.harness import fingerprint
    frozen['runtime_code']=fingerprint()
    write_json(output/'frozen.json',frozen);results=[]
    for task in tasks:
        if fingerprint()!=frozen['runtime_code'] or any(sha256_file(ROOT/n)!=h for n,h in frozen['code_sha256'].items()):raise ValueError('B0 implementation changed during execution')
        directory=output/task['case_id'];directory.mkdir()
        runtime=json.loads((dataset_directory/task['runtime']).read_text());reference=json.loads((dataset_directory/task['evaluation']).read_text())
        start=time.monotonic();answer=None;validation=None;trace=[];error=None
        try:answer,validation,trace=asyncio.run(collect_b0(runtime,directory))
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        graded=score(runtime,reference,answer,host_accepted=validation is not None)
        result=dict(case_id=task['case_id'],condition='B0',execution_kind='deterministic_code_NO_LLM',answer=answer,validation=validation,
                    assessment=graded,error=error,elapsed_seconds=round(time.monotonic()-start,3),
                    tool_attempts=len(trace),access_cost_units=sum(t.get('cost_units',0) for t in trace),model_requests=0,estimated_cny='0')
        write_json(directory/'result.json',result);results.append(result)
        print(json.dumps(dict(case_id=task['case_id'],condition='B0',host_accepted=graded['host_accepted'],quality=graded['quality'],error=error)),flush=True)
    summary=dict(fold=fold,condition='B0',cases=len(results),host_accepted=sum(r['assessment']['host_accepted'] for r in results),
                 strict_reference_passed=sum(r['assessment']['strict_reference_passed'] for r in results),
                 mean_quality=sum(r['assessment']['quality'] for r in results)/len(results),
                 model_requests=0,estimated_cny='0',results=results)
    write_json(output/'summary.json',summary);return summary
