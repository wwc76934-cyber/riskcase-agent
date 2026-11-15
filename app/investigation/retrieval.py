"""Version/time/scope filtering BEFORE BM25, dense retrieval and cross-encoder ranking."""
from collections import Counter
from datetime import datetime
import math
import re
from copy import deepcopy
from app.investigation.contracts import digest


def tokens(text):
    result=re.findall(r'[a-z0-9]+',text.lower())
    for run in re.findall(r'[\u4e00-\u9fff]+',text):result.extend(run[i:i+2] for i in range(len(run)-1))
    return result


def stamp(value):
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None:raise ValueError('Timezone required')
    return dt.timestamp()


def eligible(documents,scope,as_of):
    cutoff=stamp(as_of);latest={}
    for doc in documents:
        if digest(doc['paragraphs'])!=doc['content_hash']:raise ValueError('Document content changed')
        if doc['scope']!=scope or stamp(doc['available_at'])>cutoff:continue
        key=doc['identity'];prior=latest.get(key)
        if prior is None or doc['version']>prior['version']:latest[key]=doc
        elif doc['version']==prior['version'] and doc!=prior:raise ValueError('Conflicting version')
    # A tombstone supersedes older content; never fall back to the old version.
    return [deepcopy(d) for _,d in sorted(latest.items()) if not d.get('withdrawn',False)]


def chunks(documents):
    return [dict(citation=f'D:{d["identity"]}:v{d["version"]}:p{i+1}',document=d['identity'],version=d['version'],
                 paragraph=i+1,text=p,source=d['source'],available_at=d['available_at'],scope=d['scope'],content_hash=d['content_hash'])
            for d in documents for i,p in enumerate(d['paragraphs'])]


def bm25(query,rows):
    counts=[Counter(tokens(r['text'])) for r in rows];q=set(tokens(query));n=len(rows)
    avg=sum(sum(c.values()) for c in counts)/max(n,1)
    scores=[]
    for c in counts:
        size=sum(c.values());value=0.
        for word in q:
            tf=c[word]
            if not tf:continue
            df=sum(word in x for x in counts);idf=math.log(1+(n-df+.5)/(df+.5))
            value+=idf*tf*2.2/(tf+1.2*(.25+.75*size/max(avg,1)))
        scores.append(value)
    return scores


POLICIES={
    'v1':dict(dense_floor=.75,rerank_floor=0,provenance='frozen_before_first_development_run'),
    'v2':dict(dense_floor=.45,rerank_floor=None,provenance='retrospective_development_tuning_not_holdout'),
}


def search(documents,query,*,scope,as_of,mode='lexical',backend=None,limit=3,policy='v1'):
    if not isinstance(query,str) or not 1<=len(query.strip())<=300 or type(limit)is not int or not 1<=limit<=5:raise ValueError('Bounded query required')
    if mode not in ('lexical','hybrid','hybrid_rerank'):raise ValueError('Unknown retrieval mode')
    if policy not in POLICIES:raise ValueError('Unknown retrieval policy')
    settings=POLICIES[policy]
    selected=eligible(documents,scope,as_of);rows=chunks(selected)
    lexical=bm25(query,rows);dense=None;reranked=None
    if mode!='lexical':
        if backend is None:raise ValueError('Explicit local encoder required; no silent fallback')
        dense=list(map(float,backend.similarities(query,[r['text'] for r in rows]))) if rows else []
        if len(dense)!=len(rows) or any(not math.isfinite(x) for x in dense):raise ValueError('Invalid embedding scores')
    order=sorted(range(len(rows)),key=lambda i:(-lexical[i],rows[i]['citation']))
    candidates=[i for i in order if lexical[i]>0][:6]
    if dense is not None:
        vector_order=sorted(range(len(rows)),key=lambda i:(-dense[i],rows[i]['citation']))[:6]
        # Frozen experimental cutoff, not a calibrated probability or universal threshold.
        vector_order=[i for i in vector_order if dense[i]>=settings['dense_floor']]
        fusion={i:1/(60+rank) for rank,i in enumerate(candidates,1)}
        for rank,i in enumerate(vector_order,1):fusion[i]=fusion.get(i,0)+1/(60+rank)
        candidates=sorted(fusion,key=lambda i:(-fusion[i],rows[i]['citation']))[:6]
    candidate_scores=[]
    if mode=='hybrid_rerank' and candidates:
        reranked=list(map(float,backend.rerank(query,[rows[i]['text'] for i in candidates])))
        if len(reranked)!=len(candidates) or any(not math.isfinite(x) for x in reranked):raise ValueError('Invalid reranker scores')
        candidate_scores=[dict(citation=rows[i]['citation'],logit=score) for score,i in zip(reranked,candidates)]
        # Pairwise ranking logits are NOT answerability probabilities. V1's zero
        # cutoff is retained for reproducibility; V2 only orders eligible candidates.
        candidates=[i for score,i in sorted(zip(reranked,candidates),key=lambda pair:(-pair[0],rows[pair[1]]['citation']))
                    if settings['rerank_floor'] is None or score>=settings['rerank_floor']]
    hits=[rows[i]|dict(lexical_score=lexical[i],dense_score=dense[i] if dense is not None else None) for i in candidates[:limit]]
    return dict(status='retrieved' if hits else 'insufficient_knowledge',advisory_only=True,mode=mode,hits=hits,
                eligible_corpus_hash=digest(selected),scope=scope,as_of=as_of,policy=policy,policy_settings=settings,
                candidate_rerank_scores=candidate_scores,
                model_identity=backend.identity if backend is not None else None)
