"""Fixed pre-registered category priors, with matched non-auxiliary initialization."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
import torch

from app.risk.semantic_detection.common import digest,write_json
from .semi_fit import state_fingerprint
from .semi_supervised import MaskedBroadModel


def normalize(values):
    result=np.asarray(values,dtype=np.float32).copy()
    if result.ndim!=2 or result.shape[1]!=32 or not np.isfinite(result).all():
        raise ValueError('Finite registered32-dimensional category vectors required')
    result/=np.maximum(np.linalg.norm(result,axis=1,keepdims=True),1e-12)
    result[0]=0.;return result


def build_tables(bank,prep,output):
    bank,output=Path(bank),Path(output);output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((bank/'manifest.json').read_text(encoding='utf8'))
    spec=next(s for s in manifest['specifications'] if s['name']=='nochannel-original')
    if spec['channel'] is not False or [tuple(k) for k in spec['keys']]!=prep.keys:
        raise ValueError('Exactly the audited no-channel training category vocabulary required')
    expected='12e45b3c887b46fac53ea997284079a1b4f54e10aedf92b667d8a18b891bd3ed'
    if digest(bank/'nochannel-original.npy')!=expected or spec['vectors_sha256']!=expected:
        raise ValueError('Previously audited Qwen cache differs')
    vectors=np.load(bank/'nochannel-original.npy',allow_pickle=False)
    if vectors.shape[0]!=len(prep.keys) or vectors.shape[1]<32:raise ValueError('Qwen cache shape differs')
    tables={'qwen_frozen32':normalize(vectors[:,:32]),
            'random_frozen32':normalize(np.random.default_rng(20261102).normal(size=(len(prep.keys),32)))}
    texts=spec['texts'][1:]
    tfidf=TfidfVectorizer(lowercase=True,ngram_range=(1,2),sublinear_tf=True,dtype=np.float64)
    sparse=tfidf.fit_transform(texts)
    if min(sparse.shape)<33:raise ValueError('Registered32-component TF-IDF projection requires adequate vocabulary')
    svd=TruncatedSVD(n_components=32,random_state=20261102)
    projected=svd.fit_transform(sparse)
    tables['tfidf_frozen32']=normalize(np.vstack([np.zeros((1,32)),projected]))
    for name,table in tables.items():np.save(output/(name+'.npy'),table)
    record={'source_manifest_sha256':digest(bank/'manifest.json'),'cache_sha256':expected,
        'keys':spec['keys'],'texts':spec['texts'],'shape':[len(prep.keys),32],
        'table_sha256':{name:digest(output/(name+'.npy')) for name in tables},
        'text_sha256':hashlib.sha256(json.dumps(spec['texts'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'TFIDF_training_category_documents':len(texts),'TFIDF_vocabulary_size':len(tfidf.vocabulary_),
        'TFIDF_projection_explained_variance_ratio_sum':float(svd.explained_variance_ratio_.sum()),
        'no_labels_or_raw_transactions':True,'no_encoder_or_generation_calls':True,'paid_calls':0,
        'scope':'MCC category-ID functions only; retrospective prior comparison, not new C2 business-text data'}
    write_json(output/'manifest.json',record);return tables,record


def initialized_frozen_model(config,prep,dimensions,table):
    """Preserve the original ordinary-model RNG and all downstream initial weights."""
    ordinary=MaskedBroadModel(config,prep,dimensions)
    ordinary_hash=state_fingerprint(ordinary.state_dict())
    with torch.random.fork_rng(devices=[]):
        frozen=MaskedBroadModel(replace(config,representation='semantic'),prep,dimensions)
    frozen.load_state_dict(ordinary.state_dict())
    vectors=torch.as_tensor(table,dtype=torch.float32)
    if vectors.shape!=ordinary.point.aux.weight.shape or vectors.shape[1]!=32 or not torch.isfinite(vectors).all() or torch.count_nonzero(vectors[0]):
        raise ValueError('Aligned finite category table with zero unknown required')
    with torch.no_grad():frozen.point.aux.weight.copy_(vectors)
    frozen.point.aux.weight.requires_grad_(False)
    for key,value in ordinary.state_dict().items():
        if key!='point.aux.weight' and not torch.equal(value,frozen.state_dict()[key]):
            raise ValueError('Non-auxiliary initial parameters changed')
    return frozen,{'ordinary_cold_state_sha256':ordinary_hash,'non_auxiliary_initial_values_matched':True,
        'auxiliary_table_frozen':True,'auxiliary_parameters':vectors.numel(),
        'total_parameters':sum(p.numel() for p in frozen.parameters()),
        'trainable_parameters':sum(p.numel() for p in frozen.parameters() if p.requires_grad)}
