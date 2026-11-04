"""Static, sequence-grouped research; no user/time-generalization claim."""
import hashlib
import json
from pathlib import Path
import itertools
from collections import defaultdict
import numpy as np
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.metrics import average_precision_score,precision_recall_fscore_support
from .baselines import events,event_key


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,obj):Path(path).write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf8')


def read_records(path):
    rows=[];bad=[]
    with Path(path).open(encoding='utf8') as f:
        for line in f:
            r=json.loads(line)
            if set(r)-{'row_id','valid_sequence','actions','times','execution_time','Amount','device_freq','ip_freq','beneficiary_freq','application_freq','is_fraud'}:
                raise ValueError('Unexpected fields in converted records')
            if not r['valid_sequence']:bad.append(r);continue
            if r['is_fraud'] not in (0,1) or not events(r) or len(r['actions'])!=len(r['times']):raise ValueError('Data contract mismatch')
            rows.append(r)
    if len(rows)!=105302 or len(bad)!=1 or sum(r['is_fraud'] for r in rows)!=101 or bad[0]['is_fraud']!=0:
        raise ValueError('Published source aggregates changed')
    return rows,bad


def static_split(rows,seed=20261104):
    y=np.asarray([r['is_fraud'] for r in rows]);groups=np.asarray([event_key(r) for r in rows]);idx=np.arange(len(rows))
    pool,test=next(StratifiedGroupKFold(5,shuffle=True,random_state=seed).split(idx,y,groups))
    train_local,dev_local=next(StratifiedGroupKFold(4,shuffle=True,random_state=seed+1).split(pool,y[pool],groups[pool]))
    result={'train':pool[train_local],'development':pool[dev_local],'test':test}
    sets={k:set(groups[v]) for k,v in result.items()}
    if any(sets[a]&sets[b] for a,b in itertools.combinations(sets,2)):raise ValueError('Exact sequence overlap')
    if any(y[v].sum()<10 for v in result.values()):raise ValueError('Fixed split has insufficient positives; stop rather than choose favorable seed')
    return result


def near_audit(rows,split,max_pairs=2_000_000,max_bucket=256):
    """Deterministic64-minhash/16bands candidate screen; overflow is reported, never hidden."""
    owner={int(i):k for k,ids in split.items() for i in ids};unique={}
    for i,r in enumerate(rows):unique.setdefault(event_key(r),(i,events(r)))
    prime=np.uint64(4294967311);rng=np.random.default_rng(20261104)
    a=rng.integers(1,int(prime),64,dtype=np.uint64);b=rng.integers(0,int(prime),64,dtype=np.uint64)
    buckets=defaultdict(list);shingles={};overflows=0
    for key,(i,seq) in unique.items():
        grams={tuple(seq[j:j+3]) for j in range(max(1,len(seq)-2))}
        vals=np.asarray([int.from_bytes(hashlib.sha256(repr(g).encode()).digest()[:4],'little') for g in grams],dtype=np.uint64)
        sig=np.min((vals[:,None]*a[None]+b[None])%prime,axis=0);shingles[key]=grams
        for band in range(16):
            bucket=buckets[(band,sig[4*band:4*band+4].tobytes())]
            if len(bucket)<=max_bucket:bucket.append(key)
    pairs=set();examples=[];hits=0
    for bucket in buckets.values():
        if len(bucket)>max_bucket:overflows+=1;continue
        for x,y in itertools.combinations(bucket,2):
            if x>y:x,y=y,x
            if (x,y) in pairs:continue
            if len(pairs)>=max_pairs:
                return {'complete_candidate_screen':False,'reason':'pair_budget','pairs_checked':len(pairs),'cross_split_near_pairs':hits,'overflow_buckets':overflows,'examples_hashes_only':examples}
            pairs.add((x,y));i,sx=unique[x];j,sy=unique[y]
            if owner[i]==owner[j]:continue
            score=len(shingles[x]&shingles[y])/len(shingles[x]|shingles[y])
            if score>=.9:
                hits+=1
                if len(examples)<20:examples.append({'a':x,'b':y,'similarity':score,'split_a':owner[i],'split_b':owner[j]})
    return {'complete_candidate_screen':True,'method':'64minhash16bands4,3gramsetJaccard>=.9; approximate retrieval, not exhaustive proof','pairs_checked':len(pairs),'cross_split_near_pairs':hits,'overflow_buckets':overflows,'examples_hashes_only':examples}


def metrics(y,scores,threshold):
    y=np.asarray(y);scores=np.asarray(scores);k=max(1,int(np.ceil(.01*len(y))));chosen=np.lexsort((np.random.default_rng(20261104).random(len(y)),-scores))[:k]
    p,r,f,_=precision_recall_fscore_support(y,scores>=threshold,average='binary',zero_division=0)
    tp=int(y[chosen].sum());return {'AP':float(average_precision_score(y,scores)),'precision':float(p),'recall':float(r),'F1':float(f),'threshold':float(threshold),
        'topK':k,'topK_TP':tp,'topK_FP':k-tp,'topK_precision':tp/k,'topK_recall':tp/int(y.sum()),'rows':len(y),'positives':int(y.sum()),'budget':'1percent of static batch, not daily'}


def development_threshold(y,scores):
    from sklearn.metrics import precision_recall_curve
    p,r,t=precision_recall_curve(y,scores);f=2*p[:-1]*r[:-1]/np.maximum(p[:-1]+r[:-1],1e-12)
    return float(t[int(np.argmax(f))])
