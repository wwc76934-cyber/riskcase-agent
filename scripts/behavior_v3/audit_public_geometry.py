"""Describe public event-vector geometry without loading transactions or labels."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(values, names, pairs):
    x = np.asarray(values, dtype=np.float32)
    norm = np.linalg.norm(x,axis=1,keepdims=True)
    if np.any(norm==0) or not np.isfinite(x).all() or len(x)!=len(names):
        raise ValueError('Malformed event vector rows')
    x = x/norm
    distances = np.sum(x[pairs[:,0]]*x[pairs[:,1]],axis=1)
    centered = x-x.mean(axis=0)
    covariance = centered.T @ centered
    participation = np.trace(covariance)**2 / np.sum(covariance**2)
    nearest = []
    for start in range(0,len(x),128):
        scores = x[start:start+128] @ x.T
        rows = np.arange(len(scores))
        scores[rows,start+rows] = -np.inf
        nearest.extend(scores.argmax(axis=1).tolist())
    namespace = np.array([name.strip('/').split('/')[0] for name in names])
    return {'dimensions':x.shape[1], 'pair_cosine_p05_p50_p95':np.quantile(distances,[.05,.5,.95]).tolist(),
            'centroid_norm':float(np.linalg.norm(x.mean(axis=0))), 'effective_rank_participation_ratio':float(participation),
            'top1_neighbor_same_public_namespace':float(np.mean(namespace==namespace[nearest])),
            'top1_near_duplicate_at_cosine_0_99':int(np.sum(np.sum(x*x[nearest],axis=1)>=.99))}


def main():
    parser=argparse.ArgumentParser()
    for name in ('bank','dictionary','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads((args.bank/'manifest.json').read_text(encoding='utf8'))
    if manifest['input_bank_records'] or manifest['input_labels'] or sha(args.dictionary)!=manifest['dictionary_sha256']:
        raise ValueError('Public dictionary and public-only vector receipt required')
    with args.dictionary.open(encoding='utf-8-sig',newline='') as stream:
        names=[row['Name'] for row in csv.DictReader(stream)]
    rng=np.random.default_rng(20261109)
    pairs=rng.integers(0,len(names),size=(5000,2))
    pairs=pairs[pairs[:,0]!=pairs[:,1]]
    full=np.load(args.bank/'qwen-full.npy',allow_pickle=False)
    results={}
    for key in ('qwen','random','tfidf_svd'):
        path=args.bank/(key+'.npy')
        if sha(path)!=manifest['table_sha256'][key]:
            raise ValueError('Frozen input table changed: '+key)
        results[key+'_32']=describe(np.load(path,allow_pickle=False)[2:],names,pairs)
    for dimensions in (128,1024):
        results[f'qwen_{dimensions}_geometry_only']=describe(full[:,:dimensions],names,pairs)
    with args.output.open('x',encoding='utf8') as stream:
        json.dump({'sampled_public_pairs':len(pairs), 'dictionary_sha256':manifest['dictionary_sha256'],
            'full_vectors_sha256':sha(args.bank/'qwen-full.npy'), 'representations':results,
            'scope':'Public-name geometry only, no task refits. Namespace is a lexical proxy, not independently verified operation intent.'},
            stream,indent=2,allow_nan=False)


if __name__=='__main__':
    main()
