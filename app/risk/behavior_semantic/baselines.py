"""Track A only. Inputs are inert normalized records, never pickle objects."""
import copy
import hashlib
import math
import random
import time

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import LinearSVC
from sklearn.metrics import average_precision_score
import torch
from torch import nn

NUMERIC=('Amount','device_freq','ip_freq','beneficiary_freq','application_freq')


def events(row):
    return [v for group in row['actions'] for v in group]


def event_key(row):
    return hashlib.sha256(' '.join(map(str,events(row))).encode()).hexdigest()


def numeric(rows):
    # Published profiles are retrospective covariates, not independently verified as-of.
    return np.asarray([[math.log1p(max(0.,float(r['Amount']))),*[float(r[k]) for k in NUMERIC[1:]]] for r in rows],dtype=np.float32)


def docs(rows):return [' '.join('e'+str(g[0]) if g else '__missing_operation__' for g in r['actions']) for r in rows]


class Classical:
    def __init__(self,kind,seed=42,C=1.,balanced=True):
        if kind not in ('numeric_lr','numeric_tree','tfidf_lr','tfidf_svm','combined_lr'):raise ValueError('Registered classical model required')
        self.kind,self.seed,self.C,self.balanced=kind,seed,C,balanced

    def fit(self,rows,y):
        weight='balanced' if self.balanced else None
        if self.kind in ('numeric_lr','numeric_tree','combined_lr'):
            self.prep=make_pipeline(SimpleImputer(),StandardScaler());n=self.prep.fit_transform(numeric(rows))
        if self.kind in ('tfidf_lr','tfidf_svm','combined_lr'):
            self.vectorizer=TfidfVectorizer(tokenizer=str.split,preprocessor=None,token_pattern=None,lowercase=False,
                ngram_range=(1,3),min_df=1,sublinear_tf=True,max_features=50000,dtype=np.float32)
            text=self.vectorizer.fit_transform(docs(rows))
        X=n if self.kind.startswith('numeric') else sparse.hstack([text,sparse.csr_matrix(n)],format='csr') if self.kind=='combined_lr' else text
        if self.kind=='numeric_tree':self.model=RandomForestClassifier(n_estimators=200,max_depth=50,min_samples_leaf=2,n_jobs=2,class_weight=weight,random_state=self.seed)
        elif self.kind=='tfidf_svm':self.model=LinearSVC(C=self.C,class_weight=weight,random_state=self.seed,max_iter=5000)
        else:self.model=LogisticRegression(C=self.C,class_weight=weight,max_iter=1000,random_state=self.seed,solver='liblinear')
        self.model.fit(X,y);return self

    def predict(self,rows):
        if self.kind.startswith('numeric') or self.kind=='combined_lr':n=self.prep.transform(numeric(rows))
        if self.kind.startswith('tfidf') or self.kind=='combined_lr':text=self.vectorizer.transform(docs(rows))
        X=n if self.kind.startswith('numeric') else sparse.hstack([text,sparse.csr_matrix(n)],format='csr') if self.kind=='combined_lr' else text
        return self.model.decision_function(X) if self.kind=='tfidf_svm' else self.model.predict_proba(X)[:,1]


class BehaviorNet(nn.Module):
    def __init__(self,vocab,kind='gru',numeric_width=0,vectors=None,use_intervals=True):
        super().__init__()
        if kind not in ('gru','transformer'):raise ValueError('Registered temporal architecture required')
        self.kind,self.use_intervals=kind,use_intervals
        self.embedding=nn.Embedding(vocab,32,padding_idx=0)
        if vectors is not None:
            values=torch.as_tensor(vectors,dtype=torch.float32)
            if values.shape!=(vocab,32) or not torch.isfinite(values).all() or torch.count_nonzero(values[0]):raise ValueError('Aligned frozen32 table and zero padding required')
            with torch.no_grad():self.embedding.weight.copy_(values)
            self.embedding.weight.requires_grad_(False)
        self.project=nn.Linear(32,64);self.time=nn.Linear(1,64,bias=False)
        if kind=='gru':self.encoder=nn.GRU(64,64,batch_first=True)
        else:self.encoder=nn.TransformerEncoder(nn.TransformerEncoderLayer(64,4,128,.1,batch_first=True),2,enable_nested_tensor=False)
        self.head=nn.Sequential(nn.Linear(64+numeric_width,64),nn.ReLU(),nn.Dropout(.1),nn.Linear(64,1))

    def forward(self,ids,intervals,lengths,numbers):
        h=self.project(self.embedding(ids));mask=torch.arange(ids.shape[1],device=ids.device)[None,:]>=lengths[:,None]
        if self.use_intervals:h=h+self.time(torch.log1p(intervals.clamp_min(0)).unsqueeze(-1))
        if self.kind=='gru':
            packed=nn.utils.rnn.pack_padded_sequence(h,lengths.cpu(),batch_first=True,enforce_sorted=False)
            _,state=self.encoder(packed);z=state[-1]
        else:
            pos=torch.arange(h.shape[1],device=h.device).float()[:,None];freq=torch.exp(torch.arange(0,64,2,device=h.device)*(-math.log(10000.)/64))
            pe=torch.zeros(h.shape[1],64,device=h.device);pe[:,0::2]=torch.sin(pos*freq);pe[:,1::2]=torch.cos(pos*freq)
            h=self.encoder(h+pe[None],src_key_padding_mask=mask);z=h[torch.arange(len(h)),lengths-1]
        return self.head(torch.cat([z,numbers],dim=1)).squeeze(-1)


def prepare(rows,lookup,max_length=256,order_shuffle=False,seed=1729):
    sequences=[];times=[];rng=random.Random(seed)
    for r in rows:
        if any(len(g)>1 for g in r['actions']):raise ValueError('Multi-event timestamp group requires explicit alignment policy')
        pairs=list(zip([g[0] if g else None for g in r['actions']],r['times']))
        if len(pairs)!=len(r['times']) or not pairs:raise ValueError('Aligned nonempty sequence required')
        if order_shuffle:rng.shuffle(pairs)
        pairs=pairs[-max_length:];sequences.append([lookup.get(v,1) for v,_ in pairs]);times.append([float(t)/1000 for _,t in pairs])
    return sequences,times


def batch(sequences,times,indices,numbers):
    L=max(len(sequences[i]) for i in indices);ids=torch.zeros(len(indices),L,dtype=torch.long);dt=torch.zeros(len(indices),L);lengths=[]
    for j,i in enumerate(indices):
        n=len(sequences[i]);ids[j,:n]=torch.tensor(sequences[i]);dt[j,:n]=torch.tensor(times[i]);lengths.append(n)
    return ids,dt,torch.tensor(lengths),torch.from_numpy(numbers[indices])


def score_net(model,sequences,times,numbers,batch_size=256):
    model.eval();out=np.empty(len(sequences),np.float32);order=np.argsort([len(s) for s in sequences],kind='stable')
    with torch.inference_mode():
        for start in range(0,len(sequences),batch_size):
            idx=order[start:start+batch_size];out[idx]=model(*batch(sequences,times,idx,numbers)).sigmoid().numpy()
    return out


def fit_net(train,y,dev,dev_y,*,kind,seed=42,max_epochs=30,patience=5,with_numeric=False,vectors=None,use_intervals=True,vocab_ids=None,checkpoint_dir=None):
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.set_num_threads(2)
    lookup={v:i+2 for i,v in enumerate(sorted(set(vocab_ids) if vocab_ids is not None else {x for r in train for x in events(r)}))}
    model=BehaviorNet(len(lookup)+2,kind,5 if with_numeric else 0,vectors,use_intervals)
    prep=make_pipeline(SimpleImputer(),StandardScaler()).fit(numeric(train)) if with_numeric else None
    n=prep.transform(numeric(train)).astype(np.float32) if prep else np.zeros((len(train),0),np.float32)
    dn=prep.transform(numeric(dev)).astype(np.float32) if prep else np.zeros((len(dev),0),np.float32)
    s,t=prepare(train,lookup);ds,dt=prepare(dev,lookup)
    opt=torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),lr=.001,weight_decay=.01)
    target=torch.as_tensor(y,dtype=torch.float32);loss_fn=nn.BCEWithLogitsLoss(pos_weight=torch.tensor(20.))
    best=-1.;state=None;curves=[];stale=0;started=time.monotonic();cpu=time.process_time();rng=np.random.default_rng(seed)
    for epoch in range(1,max_epochs+1):
        model.train();total=0.;order=rng.permutation(len(train));batches=[]
        for start in range(0,len(train),4096):
            pool=sorted(order[start:start+4096],key=lambda i:len(s[i]));batches.extend(np.asarray(pool[j:j+256]) for j in range(0,len(pool),256))
        rng.shuffle(batches)
        for idx in batches:
            opt.zero_grad(set_to_none=True);loss=loss_fn(model(*batch(s,t,idx,n)),target[idx]);loss.backward();nn.utils.clip_grad_norm_(model.parameters(),5.);opt.step();total+=loss.item()*len(idx)
        ap=float(average_precision_score(dev_y,score_net(model,ds,dt,dn)));curves.append({'epoch':epoch,'loss':total/len(train),'development_AP':ap})
        if checkpoint_dir is not None:
            from pathlib import Path
            import json
            directory=Path(checkpoint_dir)
            torch.save({'epoch':epoch,'model':model.state_dict(),'optimizer':opt.state_dict(),'torch_rng':torch.get_rng_state(),'seed':seed},directory/f'epoch-{epoch:02d}.pt')
            (directory/'curves.json').write_text(json.dumps(curves,indent=2),encoding='utf8')
        print(f'{kind} seed{seed} epoch{epoch} AP={ap:.6f}',flush=True)
        if ap>best:best=ap;state=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        if stale>=patience:break
    model.load_state_dict(state)
    return model,lookup,prep,{'curves':curves,'train_wall_seconds':time.monotonic()-started,'train_CPU_seconds':time.process_time()-cpu,'parameters':sum(p.numel() for p in model.parameters()),'trainable_parameters':sum(p.numel() for p in model.parameters() if p.requires_grad),'seed':seed,'best_development_AP':best,'test_seen_by_trainer':False}
