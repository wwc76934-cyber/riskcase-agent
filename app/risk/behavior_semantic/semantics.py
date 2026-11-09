"""Public event-ontology descriptions. Never accepts transactions or labels."""
import csv
import hashlib
import json
import re
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD

VERBS={'GET':'retrieve','FETCH':'retrieve','CREATE':'create','DELETE':'delete','UPDATE':'update',
       'VALIDATE':'validate','CONFIRM':'confirm','SUBMIT':'submit','LOAD':'load','LIST':'list','REGISTER':'register',
       'SEARCH':'search','CHECK':'check','SAVE':'save','CANCEL':'cancel','EXPORT':'export','PAY':'pay'}


def description(endpoint):
    if not isinstance(endpoint,str) or not re.fullmatch(r'/?[A-Za-z0-9_.{}-]+(?:/[A-Za-z0-9_.{}-]+)*/?',endpoint):
        raise ValueError('Public API path required')
    parts=endpoint.strip('/').split('/');op=parts[-1]
    verb=next((v for v in sorted(VERBS,key=len,reverse=True) if op.upper().startswith(v)),None)
    literal=(VERBS[verb]+' '+op[len(verb):]) if verb else op
    return f'Banking API operation. Namespace: {" / ".join(parts[:-1])}. Operation: {literal}. Original endpoint: {endpoint}.'


def ontology(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if set(rows[0])!={'Name'}:raise ValueError('Public dictionary schema changed')
    texts=[description(r['Name']) for r in rows]
    if len(texts)!=1916 or len(set(r['Name'] for r in rows))!=1916:raise ValueError('Published dictionary cardinality changed')
    return texts


def normalize(values):
    values=np.asarray(values,dtype=np.float32).copy();values/=np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-12)
    values[:2]=0.;return values


def static_tables(texts,qwen):
    if np.shape(qwen)!=(1918,32):raise ValueError('Frozen public-ontology32 table required')
    rng=np.random.default_rng(20261104)
    random=normalize(rng.normal(size=(1918,32)))
    X=TfidfVectorizer(ngram_range=(1,3),sublinear_tf=True).fit_transform(texts)
    lexical=normalize(np.vstack([np.zeros((2,32)),TruncatedSVD(32,random_state=20261104).fit_transform(X)]))
    shuffled=np.asarray(qwen).copy();shuffled[2:]=shuffled[2:][rng.permutation(1916)]
    return {'qwen':normalize(qwen),'random':random,'tfidf_svd':lexical,'description_shuffle':shuffled}


def history_shuffle(rows):
    result=[]
    for r in rows:
        copy=dict(r);pairs=list(zip(r['actions'],r['times']));n=len(pairs)
        seed=int(hashlib.sha256(('trackA-history-shuffle:'+r['row_id']).encode()).hexdigest()[:16],16)
        order=np.random.default_rng(seed).permutation(max(0,n-1)).tolist()+([n-1] if n else [])
        copy['actions']=[pairs[i][0] for i in order]
        # Intervals are removed for order controls: reassignment is not a real time axis.
        copy['times']=[0.]*n;result.append(copy)
    return result
