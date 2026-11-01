"""Checks protect causal comparisons, channel exclusion and frozen ranking contracts."""
import numpy as np
import pandas as pd
import pytest
import torch

from app.risk.ml_models import MODEL_COLUMNS
from app.risk.neural_model import seed_training
from app.risk.semantic_detection.evaluation import Ranking
from app.risk.semantic_detection.v2.channel_audit import daily_ranks, population, drift
from app.risk.semantic_detection.v2.matched import Config, Model, Preprocessor
from app.risk.semantic_detection.v2.representations import texts_for, industry_title


def frame():
    result = pd.DataFrame({k: np.arange(18, dtype=float) for k in MODEL_COLUMNS if k not in ('mcc','channel')})
    result['mcc'] = ['5411','5812','unknown'] * 6
    result['channel'] = ['Chip Transaction','Online Transaction','Swipe Transaction'] * 6
    return result[list(MODEL_COLUMNS)]


def build(representation='original', channel=False):
    config = Config(representation=representation, include_channel=channel)
    X = frame()
    keys = [('unknown','unknown')] + sorted(set((m,c if channel else 'unknown') for m,c in zip(X.mcc,X.channel)) - {('unknown','unknown')})
    prep = Preprocessor(config).fit(X,keys)
    seed_training(42,1)
    table = np.random.default_rng(9).normal(size=(len(keys),32)).astype(np.float32)
    return prep, Model(config,table,24,prep.numeric.categorical_sizes)


@pytest.mark.parametrize('representation',['original','learned','semantic','random','category_shuffle'])
def test_no_channel_excludes_both_direct_and_auxiliary_information(representation):
    prep, model = build(representation)
    X = frame(); changed=X.copy(); changed['channel']=list(reversed(X.channel.tolist()))
    a,b=prep.transform(X),prep.transform(changed)
    assert all(torch.equal(x,y) for x,y in zip(a,b))
    model.eval()
    assert torch.equal(model(*a),model(*b))


def test_auxiliary_does_not_change_common_initialization_or_rng():
    original_prep, original=build()
    after_original=torch.get_rng_state()
    for representation in ('learned','semantic','random'):
        _,model=build(representation)
        assert torch.equal(after_original,torch.get_rng_state())
        assert all(torch.equal(value,model.core.state_dict()[key]) for key,value in original.core.state_dict().items())
    original.eval(); tensors=original_prep.transform(frame())
    assert torch.equal(original(*tensors), original.core(*tensors[:2]))


@pytest.mark.parametrize('representation',['semantic','random','category_shuffle'])
def test_frozen_auxiliary_has_no_gradient_but_core_learns(representation):
    prep,model=build(representation)
    model(*prep.transform(frame())).sum().backward()
    assert model.aux.weight.requires_grad is False
    assert model.aux.weight.grad is None
    assert model.core.input[0].weight.grad.abs().sum()>0
    assert torch.equal(model.aux.weight[0],torch.zeros(32))


def test_no_channel_text_and_vocabulary_reject_hidden_channel():
    glossary={'entries':{'5411':{'title':'Grocery stores'}}}
    keys=[('5411','Chip Transaction'),('5411','Online Transaction')]
    assert texts_for(keys,glossary,channel=False)==['Merchant category: Grocery stores.']*2
    assert len(set(texts_for(keys,glossary,channel=True)))==2
    with pytest.raises(ValueError,match='Channel information'):
        Preprocessor(Config()).fit(frame(),[('unknown','unknown'),*keys])


def test_industry_boundaries_use_actual_visa_ranges():
    assert industry_title('3300','AZUL AIR')=='Airlines, passenger air transportation'
    assert industry_title('3359','PAYLESS CAR RENTAL')=='Car rental agencies'
    assert industry_title('3501','HOLIDAY INNS')=='Lodging, hotels and motels'
    assert industry_title('5912','unknown')=='unknown'


def test_daily_rank_matches_queue_including_exact_ties():
    n=40
    meta=pd.DataFrame({'transaction_id':[f't{i}' for i in range(n)],'event_time':['2018-01-01']*20+['2018-01-02']*20,'split':['validation']*n})
    ranking=Ranking(meta,np.arange(n)%2)
    scores=np.zeros(n)
    ranks=daily_ranks(ranking,scores)
    assert set(np.flatnonzero(ranks<=10))==set(ranking.selected(scores))
    for positions in ranking.groups.values():
        assert set(ranks[positions])==set(range(1,21))
    with pytest.raises(ValueError): daily_ranks(ranking,np.full(n,np.nan))


def test_population_denominators_and_drift_use_actual_counts():
    X=pd.DataFrame({'channel':['a','b']*6})
    meta=pd.DataFrame({'event_time':[f'{year}-01-01' for year in (2016,2017,2018) for _ in range(4)]})
    y=np.tile([1,0,0,0],3)
    counts=population(X,y,meta)
    assert counts.positive.sum()==3
    assert (counts.negative+counts.positive==counts.rows).all()
    assert np.allclose(counts.groupby('year').transaction_share.sum(),1)
    assert all(r['total_variation']==0 for r in drift(counts))


def test_fixed_five_epoch_fit_roundtrip_and_outcome_boundary(tmp_path):
    from app.risk.semantic_detection.v2.experiment import fit
    from app.risk.semantic_detection.v2.matched import load
    X=frame();config=Config(representation='learned')
    keys=[('unknown','unknown'),('5411','unknown'),('5812','unknown')]
    table=np.ones((3,32),dtype=np.float32)
    y=np.arange(len(X),dtype=np.int8)%2
    meta=pd.DataFrame({'transaction_id':[str(i) for i in range(len(X))],
                       'event_time':['2018-01-01']*len(X),'split':['validation']*len(X)})
    report,scores=fit(config,keys,table,X,y,X,Ranking(meta,y),tmp_path/'fit',5)
    assert len(report['curves'])==5 and report['epochs']==5
    assert report['reload_max_abs_diff']<=1e-6
    np.testing.assert_allclose(load(tmp_path/'fit/model').predict(X),scores,atol=1e-6)
    with pytest.raises(ValueError,match='fully supervised'):
        fit(config,keys,table,X,np.full(len(X),-1),X,Ranking(meta,y),tmp_path/'invalid',5)


def test_manual_AP_respects_score_ties():
    from app.risk.semantic_detection.v2.audit_matched import manual_ap
    # At the tied highest threshold precision is 1/2; next threshold adds one positive at 2/3.
    assert manual_ap(np.array([1,0,1,0]),np.array([.9,.9,.8,.1]))==pytest.approx((.5+2/3)/2)
