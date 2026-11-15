"""Transparent development retrieval comparison with frozen corpus and raw rankings."""
import argparse
import json
from pathlib import Path
import shutil
import time
from app.investigation.knowledge import corpus,retrieval_cases,SCOPE,AS_OF
from app.investigation.retrieval import search
from app.service.runtime import write_json
from app.service.store import digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--models',type=Path,required=True)
    p.add_argument('--policy',choices=['v1','v2'],default='v1')
    a=p.parse_args();a.directory.mkdir(parents=True,exist_ok=False)
    docs,queries=corpus(),retrieval_cases()
    write_json(a.directory/'inputs.json',dict(documents=docs,queries=queries,scope=SCOPE,as_of=AS_OF,
               policy=a.policy,data_kind='authored_development_not_holdout'))
    root=Path(__file__).resolve().parents[2]
    files=list((root/'app/investigation').glob('*.py'))+[Path(__file__)]
    hashes={}
    for path in files:
        target=a.directory/'source'/path.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);hashes[str(path.relative_to(root))]=digest(path.read_text(encoding='utf-8'))
    write_json(a.directory/'source-hashes.json',hashes)
    from app.investigation.neural_retrieval import LocalModels
    start=time.perf_counter();backend=LocalModels(a.models);load_seconds=time.perf_counter()-start
    shutil.copyfile(a.models,a.directory/'models.json')
    outputs=[];summaries={}
    for mode in ('lexical','hybrid','hybrid_rerank'):
        rows=[]
        for query in queries:
            start=time.perf_counter()
            result=search(docs,query['query'],scope=SCOPE,as_of=AS_OF,mode=mode,backend=None if mode=='lexical' else backend,policy=a.policy)
            elapsed=time.perf_counter()-start
            hits=result['hits'];ranks=[i+1 for i,h in enumerate(hits) if h['document']==query['relevant_document']]
            row=dict(**query,mode=mode,result=result,seconds=elapsed,reciprocal_rank=1/min(ranks) if ranks else 0,
                     recall_at_3=bool(ranks),abstained=not hits)
            rows.append(row);outputs.append(row)
            write_json(a.directory/'rankings.json',outputs)
        known=[r for r in rows if r['relevant_document']];unknown=[r for r in rows if not r['relevant_document']]
        summaries[mode]=dict(answerable=len(known),recall_at_3=sum(r['recall_at_3'] for r in known)/len(known),
            mrr_at_3=sum(r['reciprocal_rank'] for r in known)/len(known),
            no_answer_correct=sum(r['abstained'] for r in unknown),no_answer_total=len(unknown),
            mean_seconds=sum(r['seconds'] for r in rows)/len(rows))
        print(json.dumps({mode:summaries[mode]}),flush=True)
    write_json(a.directory/'summary.json',dict(metrics=summaries,model_load_seconds=load_seconds,
        limits=['24 public synthetic development queries; no generalization claim','CPU latency includes first encoder batch in hybrid; no throughput claim',
                'V1 frozen before first run; V2 adjusted on observed development scores, not independent calibration','Retrieval success is not task success']))


if __name__=='__main__':main()
