"""Bounded historical encoders and exact-parameter controls without neighbor data."""
from dataclasses import asdict, dataclass
import math

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from .matched import Config as PointConfig, Model as PointModel

STAT_NAMES=('lastK_count','amount_mean','amount_std','amount_min','amount_max','amount_median',
            'amount_abs_mean','current_to_abs_mean','current_minus_latest_amount','same_mcc_fraction',
            'latest_same_mcc','mean_age_seconds','newest_age_seconds','oldest_age_seconds')


@dataclass(frozen=True)
class Config:
    mode: str='flat'
    self_control: bool=False
    k: int=6
    seed: int=42
    width: int=64
    blocks: int=2
    dropout: float=.15
    learning_rate: float=.001
    weight_decay: float=.01
    positive_weight: float=20.
    batch_size: int=4096
    threads: int=4

    def __post_init__(self):
        if self.mode not in {'flat','statistics','mean','decay','gru','transformer','attention'}:
            raise ValueError('Declared history architecture required')
        if self.k not in (6,12,24) or self.width%4 or (self.self_control and self.mode in {'flat','statistics'}):
            raise ValueError('Frozen history/control bounds required')
        self.point_config()

    def point_config(self):
        fields={k:v for k,v in asdict(self).items() if k in PointConfig.__dataclass_fields__}
        return PointConfig(representation='learned',include_channel=False,engineered=False,**fields)


def oldest_first(prior,mask):
    """Recent-first left-aligned sampler -> oldest-first right-padded valid sequence."""
    lengths=mask.sum(1)
    index=(lengths[:,None]-1-torch.arange(mask.shape[1],device=mask.device)).clamp_min(0)
    sequence=torch.gather(prior,1,index[:,:,None].expand(-1,-1,prior.shape[2]))
    valid=torch.arange(mask.shape[1],device=mask.device)[None,:]<lengths[:,None]
    return sequence.masked_fill(~valid[:,:,None],0),valid,lengths


class Model(nn.Module):
    def __init__(self,config,table,numeric_width,categorical_sizes):
        super().__init__();self.config=config
        self.point=PointModel(config.point_config(),table,numeric_width,categorical_sizes)
        width=config.width
        positions=torch.arange(config.k,dtype=torch.float32)[:,None]
        scale=torch.exp(torch.arange(0,width,2,dtype=torch.float32)*(-math.log(10000.)/width))
        encoding=torch.zeros(config.k,width);encoding[:,0::2]=torch.sin(positions*scale);encoding[:,1::2]=torch.cos(positions*scale)
        self.register_buffer('position_encoding',encoding)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(config.seed+303)
            if config.mode=='statistics':
                self.statistics=nn.Linear(len(STAT_NAMES)*2,width,bias=False)
            elif config.mode!='flat':
                self.value=nn.Linear(width,width)
                self.gate=nn.Linear(width*2,width)
                if config.mode=='attention':
                    self.query,self.key=nn.Linear(width,width),nn.Linear(width,width)
                elif config.mode=='gru':
                    self.gru=nn.GRU(width,width,batch_first=True)
                elif config.mode=='transformer':
                    layer=nn.TransformerEncoderLayer(width,4,dim_feedforward=width*2,dropout=config.dropout,
                            activation='gelu',batch_first=True,norm_first=True)
                    self.transformer=nn.TransformerEncoder(layer,1,enable_nested_tensor=False)

    def forward(self,numeric,categories,pairs,*,history=None,statistics=None):
        hidden=self.point.encode(numeric,categories,pairs)
        mode=self.config.mode
        if mode=='flat':
            return self.point.core.head(hidden).squeeze(-1)
        if mode=='statistics':
            if statistics is None or statistics.shape!=(len(hidden),2*len(STAT_NAMES)):
                raise ValueError('Training-fitted explicit deterministic statistics required')
            return self.point.core.head(hidden+F.gelu(self.statistics(statistics))).squeeze(-1)
        if self.config.self_control:
            # No history argument, real neighbor count, age or identity is consulted.
            prior=hidden[:,None,:].expand(-1,self.config.k,-1)
            mask=torch.ones((len(hidden),self.config.k),dtype=torch.bool,device=hidden.device)
            ages=torch.arange(1,self.config.k+1,device=hidden.device).expand(len(hidden),-1)*3600.
        else:
            if history is None:raise ValueError('Strictly-past explicit neighborhood required')
            hn,hc,hp,mask,ages=history
            if hp.shape!=(len(hidden),self.config.k) or (ages[mask]<=0).any():
                raise ValueError('Strictly positive ages and frozen K required')
            prior=self.point.encode(hn.flatten(0,1),hc.flatten(0,1),hp.flatten()).reshape(len(hidden),self.config.k,-1)
        available=mask.any(1,keepdim=True)
        if mode in {'mean','decay','attention'}:
            if mode=='mean':weights=mask.float();values=prior
            elif mode=='decay':weights=(-ages/86400.).masked_fill(~mask,-1e9).softmax(1)*mask;values=prior
            else:
                values=prior+self.position_encoding[None,:,:]
                logits=(self.query(hidden)[:,None,:]*self.key(values)).sum(-1)/hidden.shape[-1]**.5
                logits=logits-torch.log1p(ages/3600.)
                weights=logits.masked_fill(~mask,-1e9).softmax(1)*mask
            weights=weights/weights.sum(1,keepdim=True).clamp_min(1e-8)
            context=(self.value(values)*weights[:,:,None]).sum(1)
        else:
            sequence,valid,lengths=oldest_first(prior,mask)
            if mode=='gru':
                packed=nn.utils.rnn.pack_padded_sequence(sequence,lengths.clamp_min(1).cpu(),batch_first=True,enforce_sorted=False)
                _,state=self.gru(packed);context=self.value(state[-1])
            else:
                safe_mask=valid.clone();safe_mask[~available.squeeze(1),0]=True
                encoded=self.transformer(sequence+self.position_encoding[None,:,:],src_key_padding_mask=~safe_mask)
                context=self.value((encoded*valid[:,:,None]).sum(1)/lengths[:,None].clamp_min(1))
        gate=self.gate(torch.cat([hidden,context],dim=1)).sigmoid()
        fused=torch.where(available,gate*hidden+(1-gate)*context,hidden)
        return self.point.core.head(fused).squeeze(-1)


class StatsScaler:
    def fit(self,values):
        if values.ndim!=2 or values.shape[1]!=len(STAT_NAMES) or not np.isfinite(values).all():
            raise ValueError('Finite fixed historical statistics required')
        x=np.sign(values)*np.log1p(np.abs(values))
        self.median=np.median(x,axis=0)
        q25,q75=np.percentile(x,[25,75],axis=0);self.scale=np.maximum(q75-q25,.25)
        return self

    def transform(self,values):
        if values.shape[1]!=len(STAT_NAMES) or not np.isfinite(values).all():raise ValueError('Invalid statistics')
        x=np.sign(values)*np.log1p(np.abs(values))
        return torch.from_numpy(np.concatenate([np.clip((x-self.median)/self.scale,-8,8),np.zeros_like(x)],axis=1).astype(np.float32))


def deterministic_statistics(frame,neighbors,ages):
    if (neighbors.shape!=ages.shape or neighbors.shape[0]!=len(frame) or not np.isfinite(ages).all()
            or (ages[neighbors>=0]<=0).any() or (ages[neighbors<0]!=0).any()):
        raise ValueError('Aligned strictly-past neighborhoods required')
    if (neighbors>=len(frame)).any() or (neighbors< -1).any():raise ValueError('Invalid neighbor index')
    amounts=frame.amount_cents.to_numpy(dtype=np.float64);mcc=frame.mcc.astype(str).to_numpy()
    output=np.zeros((len(frame),len(STAT_NAMES)),dtype=np.float64)
    for start in range(0,len(frame),16384):
        end=min(start+16384,len(frame));index=neighbors[start:end];valid=index>=0;safe=index.clip(min=0)
        values=np.where(valid,amounts[safe],0.);count=valid.sum(1);den=np.maximum(count,1)
        mean=values.sum(1)/den;abs_mean=np.abs(values).sum(1)/den
        std=np.sqrt(np.maximum((values**2).sum(1)/den-mean**2,0))
        minimum=np.zeros(len(index));maximum=minimum.copy();median=minimum.copy();has=count>0
        nonempty=np.where(valid[has],values[has],np.nan)
        minimum[has]=np.nanmin(nonempty,axis=1);maximum[has]=np.nanmax(nonempty,axis=1);median[has]=np.nanmedian(nonempty,axis=1)
        same=(mcc[safe]==mcc[start:end,None])&valid
        newest=ages[start:end,0];oldest=ages[start:end].max(1)
        result=np.column_stack([count,mean,std,minimum,maximum,median,abs_mean,
                    np.where(has,amounts[start:end]/np.maximum(abs_mean,100),0),
                    np.where(has,amounts[start:end]-values[:,0],0),same.sum(1)/den,same[:,0],
                    ages[start:end].sum(1)/den,newest,oldest])
        if not np.isfinite(result).all():raise ValueError('Nonfinite derived history')
        output[start:end]=result
    return output
