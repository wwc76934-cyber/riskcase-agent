"""Preparation primitives only; experiment orchestration must verify Gate B first.

Focal: arxiv:1708.02002; effective-number weights: arxiv:1901.05555.
The optional view changes internal context activations, never transaction facts.
"""
import hashlib
import math

import numpy as np
import pandas as pd
import torch
from torch.nn import functional as F

from .expanded_controls import BroadStatsModel


def controlled_visibility(metadata, ground_truth, *, simulation_acknowledged=False,
                          fraction=.1, seed=20261102, delay_days=7,
                          cutoff='2017-12-31T23:59:59'):
    """Explicit retrospective availability hypothesis; not observed discovery time.

    Identity hashing and eligibility depend on no labels. Only the returned
    {-1,0,1} vector, not hidden ground truth, is supplied to model training.
    """
    if simulation_acknowledged is not True:
        raise ValueError('Explicit controlled availability simulation acknowledgement required')
    y=np.asarray(ground_truth)
    if y.ndim!=1 or len(metadata)!=len(y) or not np.isin(y,[0,1]).all():
        raise ValueError('Only known binary training ground truth may be hidden')
    if not metadata.split.eq('train').all() or metadata.transaction_id.duplicated().any() or metadata.transaction_id.isna().any():
        raise ValueError('Unique training-only identities required; no development optimization')
    times=pd.to_datetime(metadata.event_time,errors='raise')
    if times.isna().any() or times.dt.tz is not None or not times.dt.year.isin([2016,2017]).all():
        raise ValueError('Known source-local synthetic training calendar required')
    if not 0<fraction<=1 or type(delay_days) is not int or delay_days<0:
        raise ValueError('Explicit bounded visibility and nonnegative delay required')
    available=times+pd.Timedelta(days=delay_days)
    eligible=(available<=pd.Timestamp(cutoff)).to_numpy()
    threshold=int(fraction*(1<<64))
    hashed=np.array([int.from_bytes(hashlib.sha256(
        f'riskcase-v21-controlled-labels-v1\0{seed}\0{tx}'.encode()).digest()[:8],'big')<threshold
        for tx in metadata.transaction_id.astype(str)],dtype=bool)
    visible=eligible&hashed
    result=np.full(len(y),-1,dtype=np.int8);result[visible]=y[visible]
    known=y[visible]
    return result, {'scope':'explicit_controlled_retrospective_simulation',
        'event_clock':'source-local synthetic calendar; no timezone inference',
        'assumed_delay_days':delay_days,'assumed_training_cutoff':cutoff,
        'visibility_fraction':fraction,'hash_seed':seed,'integer_hash_threshold':threshold,
        'training_rows':len(y),'eligible_by_assumed_delay':int(eligible.sum()),
        'visible_rows':int(visible.sum()),'visible_positive':int(known.sum()),
        'visible_negative':int((known==0).sum()),'unlabeled_rows':int((~visible).sum()),
        'visible_label_vector_sha256':hashlib.sha256(result.tobytes()).hexdigest(),
        'real_available_at_verified':False,'unknown_never_assumed_normal':True}


class CalibratedLoss:
    """Frozen class coefficients; match expected loss at reference probability .5.

    This is an analytical anchor, not a claim that the initialized network's
    actual probabilities all equal .5. It does not equalize gradient geometry.
    Counts MUST be those of the visible labels, not full/hidden targets.
    """
    def __init__(self,name,visible_labels):
        labels=np.asarray(visible_labels)
        if not np.isin(labels,[-1,0,1]).all():raise ValueError('Explicit visible labels required')
        self.negative=int((labels==0).sum());self.positive=int((labels==1).sum())
        if min(self.negative,self.positive)<1:raise ValueError('Both visible classes required')
        self.name=name;self.beta=None
        counts=np.array([self.negative,self.positive],dtype=np.float64)
        frequency=counts/counts.sum();target=float(frequency@np.array([1.,20.]))
        if name=='weighted_bce':
            self.coefficients=np.array([1.,20.]);self.gamma=0.;self.scale=1.
        elif name=='standard_focal':
            self.coefficients=np.array([.75,.25]);self.gamma=2.
            self.scale=target/float(frequency@self.coefficients*(.5**self.gamma))
        elif name=='matched_weight_focal':
            # Match BCE20's class ratio to isolate focusing from class weighting.
            # Alpha is fixed before any real-data masked experiment.
            self.coefficients=np.array([1/21,20/21]);self.gamma=2.
            self.scale=target/float(frequency@self.coefficients*(.5**self.gamma))
        elif name=='class_balanced_focal':
            self.beta=.9999;self.gamma=2.
            weights=(1-self.beta)/(-np.expm1(counts*math.log(self.beta)))
            self.coefficients=weights/float(frequency@weights)
            self.scale=target/(.5**self.gamma)
        else:raise ValueError('Declared BCE / standard focal / weight-matched focal / class-balanced focal required')
        self.expected_initial_loss=target*math.log(2.)

    def values(self,logits,labels,*,scaled=True):
        if logits.shape!=labels.shape or not torch.isin(labels,torch.tensor([0.,1.],device=labels.device)).all():
            raise ValueError('Supervised objective accepts only visible binary targets')
        labels=labels.to(logits.dtype)
        if self.name=='weighted_bce':
            values=F.binary_cross_entropy_with_logits(logits,labels,
                pos_weight=logits.new_tensor(20.),reduction='none')
        else:
            ce=F.binary_cross_entropy_with_logits(logits,labels,reduction='none')
            p=logits.sigmoid();pt=torch.where(labels==1,p,1-p)
            coefficients=logits.new_tensor(self.coefficients)[labels.long()]
            values=coefficients*(1-pt).pow(self.gamma)*ce
        return values*self.scale if scaled else values

    def logit_gradient_magnitude(self,logits,labels):
        """Per-known-sample derivative before reduction or consistency terms."""
        if logits.shape!=labels.shape or not torch.isin(labels,torch.tensor([0.,1.],device=labels.device)).all():
            raise ValueError('Gradient diagnostics accept only visible binary targets')
        p=logits.sigmoid();pt=torch.where(labels==1,p,1-p)
        ce=F.binary_cross_entropy_with_logits(logits,labels.to(logits.dtype),reduction='none')
        a=logits.new_tensor(self.coefficients*self.scale)[labels.long()]
        return a*(1-pt).pow(self.gamma)*((1-pt)+self.gamma*pt*ce)

    def metadata(self):
        probes=[]
        for p in (.001,.01,.1,.5,.9):
            z=torch.tensor([math.log(p/(1-p))]*2,dtype=torch.float64)
            gradients=self.logit_gradient_magnitude(z,torch.tensor([0.,1.],dtype=torch.float64))
            probes.append({'predicted_positive_probability':p,'negative_abs_logit_gradient':float(gradients[0]),
                'positive_abs_logit_gradient':float(gradients[1])})
        return {'name':self.name,'visible_negative':self.negative,'visible_positive':self.positive,
            'gamma':self.gamma,'effective_number_beta':self.beta,
            'raw_negative_coefficient':float(self.coefficients[0]),'raw_positive_coefficient':float(self.coefficients[1]),
            'fixed_loss_scale':self.scale,'expected_initial_mean_loss_at_p_half':self.expected_initial_loss,
            'logit_gradient_probes':probes,
            'limit':'Initial expected loss matching does not equalize derivatives, curvature, class emphasis or convergence'}


def batch_objective(logits,visible_labels,teacher_logits,criterion,consistency_weight):
    """No hidden targets in this interface; teacher outputs are always detached."""
    if logits.shape!=visible_labels.shape or logits.shape!=teacher_logits.shape:
        raise ValueError('Aligned masked targets and teacher logits required')
    if not torch.isin(visible_labels,torch.tensor([-1,0,1],device=visible_labels.device)).all():
        raise ValueError('Only explicit unknown=-1 and known binary labels allowed')
    if not math.isfinite(consistency_weight) or consistency_weight<0:
        raise ValueError('Finite nonnegative consistency weight required')
    known=visible_labels>=0;unknown=~known
    supervised=criterion.values(logits[known],visible_labels[known]).mean() if known.any() else logits.sum()*0.
    target=teacher_logits.detach().sigmoid().clamp(1e-6,1-1e-6)
    # Bernoulli KL: the subtracted entropy has no student gradient.
    consistency=(F.binary_cross_entropy_with_logits(logits[unknown],target[unknown],reduction='none')-
        F.binary_cross_entropy(target[unknown],target[unknown],reduction='none')).mean() if unknown.any() else logits.sum()*0.
    return supervised+consistency_weight*consistency,supervised,consistency


def consistency_schedule(target_weight,epoch):
    if epoch<1 or target_weight<0:raise ValueError('Positive epoch and nonnegative weight required')
    return target_weight*min(1.,max(0.,(epoch-2)/3.))


class MaskedBroadModel(BroadStatsModel):
    """Identical inference architecture; optional legal internal dropout view."""
    def forward(self,numeric,categories,pairs,statistics,*,view_probability=0.,view_generator=None):
        if not 0<=view_probability<1:raise ValueError('Bounded context activation dropout required')
        hidden=self.point.encode(numeric,categories,pairs);context=self.context(statistics)
        if self.training and view_probability:
            if view_generator is None:raise ValueError('Separate view RNG must not change base-model dropout draws')
            keep=torch.rand(context.shape,generator=view_generator,device=context.device)>=view_probability
            context=context*keep/(1-view_probability)
        gate=self.gate(torch.cat([hidden,context],1)).sigmoid()
        return self.point.core.head(gate*hidden+(1-gate)*context).squeeze(-1)


@torch.no_grad()
def update_teacher(teacher,student,decay=.99):
    if not 0<=decay<1:raise ValueError('EMA decay must be below one')
    t,s=teacher.state_dict(),student.state_dict()
    if t.keys()!=s.keys():raise ValueError('Matching teacher/student architecture required')
    for name,value in t.items():
        if value.is_floating_point():value.mul_(decay).add_(s[name],alpha=1-decay)
        else:value.copy_(s[name])
