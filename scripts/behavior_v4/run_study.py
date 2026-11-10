"""Bounded FraudNLP adaptation, adjacent-event pretraining and matched numeric study."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
import torch
from torch import nn
from threadpoolctl import threadpool_limits

from app.risk.behavior_semantic.baselines import Classical, event_key, numeric
from app.risk.behavior_semantic.study import development_threshold, metrics, write
from scripts.behavior_v3.analyze_development import cluster_interval
from scripts.behavior_v3.review_blind_freeze import sha


SIZE = 1918


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def selected_records(records, split_path):
    with np.load(split_path,allow_pickle=False) as split:
        indices = [split[name].tolist() for name in ('train','development')]
    rows = [[None]*len(ids) for ids in indices]
    wanted = {index:(partition,pos) for partition,ids in enumerate(indices) for pos,index in enumerate(ids)}
    valid = 0
    with Path(records).open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line)
            if not row['valid_sequence']:
                continue
            if valid in wanted:
                partition,pos = wanted[valid]
                rows[partition][pos] = row
            valid += 1
    if valid != 105302 or any(row is None for part in rows for row in part):
        raise ValueError('Selected development inputs incomplete')
    return rows


def event_ids(row):
    return np.array([g[0]+2 if g else 1 for g in row['actions'][-256:]], dtype=np.int64)


def count_bands(sequences):
    index, values, offsets = [],[],[0]
    for seq in sequences:
        for start,end in ((0,1),(1,16),(16,64),(64,256)):
            window = seq[::-1][start:end]
            ids,counts = np.unique(window,return_counts=True)
            index.extend(ids.tolist())
            values.extend((counts/len(window)).tolist() if len(window) else [])
            offsets.append(len(index))
    return sparse.csr_matrix((np.asarray(values,np.float32),index,offsets),shape=(len(sequences)*4,SIZE))


def normalize(x):
    return (x/np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-12)).astype(np.float32)


def frozen_tables(bank, full_digest, manifest):
    full_path = bank/'qwen-full.npy'
    if sha(full_path) != full_digest:
        raise ValueError('Cached full vectors changed')
    full = np.load(full_path,allow_pickle=False)
    if full.shape != (SIZE-2,1024) or not np.isfinite(full).all():
        raise ValueError('Unexpected public Qwen matrix')
    q32 = normalize(full[:,:32])
    rng = np.random.default_rng(20261110)
    lexical_path = bank/'tfidf_svd.npy'
    if sha(lexical_path)!=manifest['table_sha256']['tfidf_svd']:
        raise ValueError('Cached same-text lexical vectors changed')
    core = {'qwen32':q32, 'qwen128':normalize(full[:,:128]),
            'qwen32_centered':normalize(q32-q32.mean(axis=0)),
            'random32':normalize(rng.normal(size=(SIZE-2,32))),
            'random128':normalize(rng.normal(size=(SIZE-2,128))),
            'tfidf_svd32':np.load(lexical_path,allow_pickle=False)[2:]}
    return {name:np.pad(table,((2,0),(0,128-table.shape[1]))).astype(np.float32) for name,table in core.items()}


def transitions(sequences):
    pairs = np.concatenate([seq[:-1]*SIZE+seq[1:] for seq in sequences])
    return np.bincount(pairs,minlength=SIZE*SIZE).reshape(SIZE,SIZE)


def domain_factor(counts):
    left,right = np.nonzero(counts)
    expectation = counts.sum(axis=1)[left]*counts.sum(axis=0)[right]/counts.sum()
    lift = np.maximum(np.log(counts[left,right]/expectation),0)
    matrix = sparse.csr_matrix((lift,(left,right)),shape=counts.shape)
    model = TruncatedSVD(32,random_state=20261110)
    representation = model.fit_transform(matrix)
    return representation,model.components_


def domain_pretraining(sequences):
    tick,cpu = time.monotonic(),time.process_time()
    source = transitions([s for i,s in enumerate(sequences) if i%10])
    holdout = transitions(sequences[::10])
    representation, head = domain_factor(source)
    scores = representation @ head
    scores[:,source.sum(axis=0)==0] = -np.inf
    predicted = scores.argmax(axis=1)
    popular = int(source.sum(axis=0).argmax())
    count = int(holdout.sum())
    accuracy = float(holdout[np.arange(SIZE),predicted].sum()/count)
    baseline = float(holdout[:,popular].sum()/count)
    initial,_ = domain_factor(source+holdout)
    initial = normalize(initial)*np.sqrt(32)
    initial[0]=0
    return initial, {'training_internal_heldout_transitions':count,'low_rank_next_event_top1':accuracy,
                     'popularity_top1':baseline,'fraud_labels_used':0,'development_rows_used':0,
                     'wall_seconds':time.monotonic()-tick,'CPU_seconds':time.process_time()-cpu}


class Adapter(nn.Module):
    def __init__(self, table, learned, numeric_width):
        super().__init__()
        self.projection = nn.Linear(128,24,bias=False)
        self.head = nn.Sequential(nn.Linear(96+numeric_width,32),nn.ReLU(),nn.Linear(32,1))
        self.embedding = nn.Embedding(SIZE,32,padding_idx=0) if learned else None
        if learned and table is not None:
            with torch.no_grad():
                self.embedding.weight.copy_(torch.from_numpy(table))
        if not learned:
            self.register_buffer('table',torch.from_numpy(table))

    def forward(self, pool, numbers):
        if self.embedding is not None:
            idx,offsets,weights = pool
            pool = nn.functional.embedding_bag(idx,self.embedding.weight,offsets,mode='sum',
                per_sample_weights=weights,include_last_offset=True)
            pool = nn.functional.pad(pool,(0,96))
        encoded = self.projection(pool.reshape(-1,4,128)).flatten(1)
        return self.head(torch.cat((encoded,numbers),dim=1)).flatten()


def make_batch(pool, index):
    if isinstance(pool,np.ndarray):
        return torch.from_numpy(pool[index])
    values = pool[(4*index[:,None]+np.arange(4)).ravel()]
    return (torch.from_numpy(values.indices.astype(np.int64)),torch.from_numpy(values.indptr.astype(np.int64)),
            torch.from_numpy(values.data))


def predict(model,pool,numbers,batch):
    model.eval()
    results = []
    with torch.inference_mode():
        for start in range(0,len(numbers),batch):
            idx = np.arange(start,min(start+batch,len(numbers)))
            results.append(model(make_batch(pool,idx),torch.from_numpy(numbers[idx])).sigmoid().numpy())
    return np.concatenate(results)


def fit_one(name,seed,policy,tables,domain,counts,numbers,labels,folder):
    torch.manual_seed(seed)
    learned = name in {'ordinary','domain'}
    table = domain if name=='domain' else tables.get(name)
    model = Adapter(table,learned,numbers[0].shape[1])
    pool = counts if learned else tuple((matrix @ table).reshape(-1,4,128) for matrix in counts)
    rng = np.random.default_rng(seed)
    optimizer = torch.optim.AdamW(model.parameters(),lr=.002,weight_decay=.01)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(20.))
    y,dy = labels
    batch = policy['batch']
    curve,best,stale = [],-1.,0
    tick,cpu = time.monotonic(),time.process_time()
    for epoch in range(1,policy['epochs']+1):
        model.train()
        total = 0.
        order = rng.permutation(len(y))
        for start in range(0,len(y),batch):
            idx = order[start:start+batch]
            optimizer.zero_grad(set_to_none=True)
            logits = model(make_batch(pool[0],idx),torch.from_numpy(numbers[0][idx]))
            loss = criterion(logits,torch.from_numpy(y[idx].astype(np.float32)))
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(),5.)
            optimizer.step()
            total += float(loss.detach())*len(idx)
        score = predict(model,pool[1],numbers[1],batch)
        ap = float(average_precision_score(dy,score))
        curve.append({'epoch':epoch,'loss':total/len(y),'development_AP':ap})
        if ap>best:
            best,stale = ap,0
            np.save(folder/'development-scores.npy',score)
            torch.save(model.state_dict(),folder/'model.pt')
        else:
            stale += 1
        write(folder/'curve.json',curve)
        if stale>=policy['patience']:
            break
    cost = {'wall_seconds':time.monotonic()-tick,'CPU_seconds':time.process_time()-cpu}
    model.load_state_dict(torch.load(folder/'model.pt',map_location='cpu',weights_only=True))
    tick = time.monotonic()
    replay = predict(model,pool[1],numbers[1],batch)
    cost['development_inference_seconds'] = time.monotonic()-tick
    if not np.array_equal(replay,np.load(folder/'development-scores.npy',allow_pickle=False)):
        raise ValueError('V4 score replay changed')
    return replay, {**cost,'epochs':len(curve),'trainable_parameters':sum(p.numel() for p in model.parameters()),
        'frozen_vector_elements':0 if learned else int(table.size),'checkpoint_sha256':sha(folder/'model.pt'),
        'development_scores_sha256':sha(folder/'development-scores.npy'), 'metrics':metrics(dy,replay,development_threshold(dy,replay))}


def lexical_controls(train,dev,y,dy):
    import joblib
    results = {}
    for name in ('tfidf_lr','combined_lr'):
        chosen = read(Path('runs/track-a-a2-01')/name/'seed-42/training.json')
        for view in ('full','suffix256'):
            tick = time.monotonic()
            model = Classical(name,42,chosen['selected_C'],chosen['selected_balanced'])
            tr,dv = (train,dev) if view=='full' else ([{**r,'actions':r['actions'][-256:]} for r in train],
                                                       [{**r,'actions':r['actions'][-256:]} for r in dev])
            model.fit(tr,y)
            elapsed = time.monotonic()-tick
            tick = time.monotonic()
            score = model.predict(dv)
            infer = time.monotonic()-tick
            folder = Path(name+'-'+view)
            results[folder.name] = (model,score,{'fit_seconds':elapsed,'inference_seconds':infer,
                'parameters':int(model.model.coef_.size+model.model.intercept_.size),
                'metrics':metrics(dy,score,development_threshold(dy,score))})
    return results,joblib.dump


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    policy,out = read(args.protocol),args.output
    if policy['status']!='v4_exploration_registered_train_development_only':
        raise ValueError('Registered V4 protocol required')
    for key in ('records','split','input_audit'):
        if sha(policy[key])!=policy[key+'_sha256']:
            raise ValueError('Frozen input changed: '+key)
    bank = Path(policy['bank'])
    if sha(bank/'manifest.json')!=policy['bank_manifest_sha256']:
        raise ValueError('Frozen public representation receipt changed')
    manifest = read(bank/'manifest.json')
    if manifest['input_bank_records'] or manifest['input_labels']:
        raise ValueError('Public names only')
    torch.set_num_threads(2)
    out.mkdir(parents=True,exist_ok=False)
    write(out/'protocol.json',policy)
    write(out/'source.json',{'runner_sha256':sha(__file__),'protocol_sha256':sha(args.protocol),
                             'torch':str(torch.__version__),'numpy':np.__version__})
    train,dev = selected_records(policy['records'],policy['split'])
    y,dy = (np.array([r['is_fraud'] for r in part]) for part in (train,dev))
    if (len(train),len(dev),int(y.sum()),int(dy.sum())) != (63410,21219,60,23):
        raise ValueError('Unexpected V4 split counts')
    groups = [event_key(r) for r in dev]
    sequences = [[event_ids(row) for row in part] for part in (train,dev)]
    counts = tuple(count_bands(part) for part in sequences)
    tables = frozen_tables(bank,policy['qwen_full_sha256'],manifest)
    prep = StandardScaler().fit(numeric(train))
    numeric_inputs = tuple(prep.transform(numeric(part)).astype(np.float32) for part in (train,dev))
    np.savez(out/'numeric_train_scaler.npz',mean=prep.mean_,scale=prep.scale_)
    domain = None
    result = {'models':{},'panels':{},'test_membership_read':False,'test_predictions_generated':False,
              'offline_encoder':{k:manifest[k] for k in ('encoder_parameters','wall_seconds','CPU_seconds','revision')},
              'new_offline_encoding_seconds':0,'paid_calls':0}
    for phase in ('gate2','gate4'):
        if phase=='gate4':
            domain,domain_stats = domain_pretraining(sequences[0])
            np.save(out/'domain_embedding.npy',domain)
            result['domain_pretraining'] = {**domain_stats,'table_sha256':sha(out/'domain_embedding.npy')}
        panel = {}
        numbers = numeric_inputs if phase=='gate4' else tuple(np.zeros((len(v),0),np.float32) for v in (train,dev))
        for name in policy[phase]:
            seed_scores = []
            for seed in policy['seeds']:
                key=f'{phase}/{name}/seed-{seed}'
                folder=out/key
                folder.mkdir(parents=True)
                score,info = fit_one(name,seed,policy,tables,domain,counts,numbers,(y,dy),folder)
                write(folder/'result.json',info)
                result['models'][key] = info
                seed_scores.append(score)
                print(key,'AP',info['metrics']['AP'],flush=True)
            ensemble = np.mean(seed_scores,axis=0)
            np.save(out/phase/name/'ensemble-development.npy',ensemble)
            panel[name] = (ensemble,seed_scores)
        contrasts = [('qwen32','ordinary'),('qwen32','random32'),('qwen32','tfidf_svd32'),
            ('qwen128','ordinary'),('qwen128','random128'),('qwen128','qwen32'),
            ('qwen32_centered','qwen32'),('qwen32_centered','ordinary'),('qwen32_centered','random32')] if phase=='gate2' else [
            ('qwen128','ordinary'),('qwen128','random128'),('domain','ordinary')]
        comparisons = {}
        for candidate,control in contrasts:
            left,ls = panel[control]
            right,rs = panel[candidate]
            comparison = cluster_interval(dy,left,right,groups)
            comparison['same_seed_AP_deltas'] = [float(average_precision_score(dy,b)-average_precision_score(dy,a)) for a,b in zip(ls,rs)]
            comparisons[candidate+'_minus_'+control] = comparison
        result['panels'][phase] = {'ensembles':{name:metrics(dy,s,development_threshold(dy,s)) for name,(s,_) in panel.items()},
                                    'comparisons':comparisons}
        write(out/(phase+'-summary.json'),result['panels'][phase])
    references,dump = lexical_controls(train,dev,y,dy)
    result['lexical'] = {}
    for name,(model,score,info) in references.items():
        folder = out/'lexical'/name
        folder.mkdir(parents=True)
        dump(model,folder/'model.joblib')
        np.save(folder/'development-scores.npy',score)
        result['lexical'][name] = {**info,'checkpoint_sha256':sha(folder/'model.joblib'),
                                  'development_scores_sha256':sha(folder/'development-scores.npy')}
    write(out/'results.json',result)


if __name__=='__main__':
    with threadpool_limits(limits=2):
        main()
