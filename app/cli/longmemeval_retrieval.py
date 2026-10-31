"""Public session-retrieval evaluation, never a claim of answer or fraud accuracy."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np
from app.investigation.retrieval import bm25
from app.service.runtime import write_json
from app.service.store import digest

SOURCE_HASH='d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442'


def date(value):
    # Benchmark dates have no timezone. Compare their declared local timeline,
    # rather than relabeling them as real UTC availability timestamps.
    clean=re.sub(r'\s+\([A-Za-z]{3}\)', '', value)
    for fmt in ('%Y/%m/%d %H:%M','%Y/%m/%d %H:%M:%S','%Y-%m-%dT%H:%M:%S'):
        try:return datetime.strptime(clean,fmt)
        except ValueError:pass
    raise ValueError('Unsupported benchmark date format')


def runtime(record):
    ids,dates,sessions=(record[k] for k in ('haystack_session_ids','haystack_dates','haystack_sessions'))
    if len(ids)!=len(dates) or len(ids)!=len(sessions):raise ValueError('Aligned sessions required')
    rows=[]
    for occurrence,(identity,stamp,turns) in enumerate(zip(ids,dates,sessions)):
        date(stamp)
        if any(t['role'] not in ('user','assistant') or not isinstance(t['content'],str) for t in turns):raise ValueError('Text conversation schema required')
        rows.append(dict(identity=identity,occurrence=occurrence,date=stamp,text=stamp+'\n'+'\n'.join(t['role']+': '+t['content'] for t in turns)))
    date(record['question_date'])
    # Whitelist construction deliberately strips question_id/type, answer,
    # answer_session_ids and per-turn has_answer before crossing this boundary.
    return dict(query=record['question'],as_of=record['question_date'],sessions=rows)


def retrieve(packet,mode):
    if set(packet)!={'query','as_of','sessions'}:raise ValueError('Only runtime fields accepted')
    if any(set(row)!={'identity','occurrence','date','text'} for row in packet['sessions']):raise ValueError('Session fields are bounded')
    cutoff=date(packet['as_of']);rows=[r for r in packet['sessions'] if date(r['date'])<=cutoff]
    if mode=='recency':order=sorted(range(len(rows)),key=lambda i:(-date(rows[i]['date']).toordinal(),-date(rows[i]['date']).hour,-date(rows[i]['date']).minute,rows[i]['identity']))
    elif mode=='bm25':
        scores=bm25(packet['query'],rows)
        order=[i for i in sorted(range(len(rows)),key=lambda i:(-scores[i],rows[i]['identity'])) if scores[i]>0]
    else:raise ValueError('Frozen comparison mode required')
    hits=[];seen=set()
    for i in order:
        if rows[i]['identity'] in seen:continue
        seen.add(rows[i]['identity']);hits.append(dict(session_id=rows[i]['identity'],characters=len(rows[i]['text'])))
        if len(hits)==5:break
    return hits


def score(hits,gold):
    if not gold:raise ValueError('Answerable questions require annotated evidence sessions')
    ids=[h['session_id'] for h in hits];out={}
    for k in (1,3,5):
        found=set(ids[:k])&gold
        out['recall@'+str(k)]=len(found)/len(gold)
        out['all_hit@'+str(k)]=int(found==gold)
    out['mrr']=next((1/(i+1) for i,key in enumerate(ids) if key in gold),0)
    return out


def aggregate(rows):
    groups={}
    for mode in ('recency','bm25'):
        selected=[r for r in rows if r['mode']==mode];answerable=[r for r in selected if not r['abstention']];abstain=[r for r in selected if r['abstention']]
        means=lambda records:{key:float(np.mean([r['scores'][key] for r in records])) for key in ('recall@1','recall@3','recall@5','all_hit@1','all_hit@3','all_hit@5','mrr')} if records else {}
        groups[mode]=dict(answerable_questions=len(answerable),abstention_questions=len(abstain),**means(answerable),
            abstention_nonempty_retrieval_rate=float(np.mean([bool(r['hits']) for r in abstain])) if abstain else None,
            returned_characters_median=float(np.median([sum(h['characters'] for h in r['hits']) for r in selected])),
            returned_characters_p95=float(np.percentile([sum(h['characters'] for h in r['hits']) for r in selected],95)),
            total_retrieval_seconds=sum(r['seconds'] for r in selected),
            by_question_type={kind:dict(questions=len(part:=[r for r in answerable if r['question_type']==kind]),**means(part)) for kind in sorted({r['question_type'] for r in answerable})})
    return groups


def run(source,output):
    if output.exists():raise ValueError('Fresh output required; keep original scores')
    output.mkdir(parents=True)
    raw=source.read_bytes();source_hash=hashlib.sha256(raw).hexdigest()
    if source_hash!=SOURCE_HASH:raise ValueError('Fixed author data checksum required')
    root=Path(__file__).resolve().parents[2]
    code={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),root/'app/investigation/retrieval.py',root/'docs/LONGMEMEVAL_RETRIEVAL_PROTOCOL.md')}
    write_json(output/'preflight.json',dict(source_sha256=source_hash,code=code,model_requests=0))
    records=json.loads(raw)
    if len(records)!=500 or len({r['question_id'] for r in records})!=500:raise ValueError('Full official 500-question split required')
    rows=[]
    for index,record in enumerate(records):
        packet=runtime(record);absent=record['question_id'].endswith('_abs');gold=set(record['answer_session_ids'])
        for mode in ('recency','bm25'):
            start=time.perf_counter();hits=retrieve(packet,mode);elapsed=time.perf_counter()-start
            rows.append(dict(question_id=record['question_id'],question_type=record['question_type'],abstention=absent,
                             mode=mode,hits=hits,seconds=elapsed,scores=None if absent else score(hits,gold),runtime_hash=digest(packet)))
        if (index+1)%50==0:print('Scored questions:',index+1,flush=True)
    write_json(output/'per-question.json',rows)
    report=dict(source_sha256=source_hash,code=code,questions=500,model_requests=0,answer_accuracy=None,
                result_kind='session_retrieval_only_NOT_official_answer_benchmark',results=aggregate(rows),
                per_question_hash=digest(rows),limits=['session_hit_not_answer_span','no_generation_or_judge','not_fraud_cases','no_threshold_calibration','long_sessions_may_exceed_product_context'])
    write_json(output/'summary.json',report);print(json.dumps(report),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('data/raw/longmemeval-s-cleaned/longmemeval_s_cleaned.json'))
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:run(args.source,args.output)
    except Exception as exc:
        if args.output.exists() and not (args.output/'failure.json').exists():write_json(args.output/'failure.json',dict(error=type(exc).__name__,detail=str(exc)))
        raise


if __name__=='__main__':main()
