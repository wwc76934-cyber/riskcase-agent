from collections import Counter

import numpy as np
import torch

from app.risk.behavior_semantic.baselines import fit_net
from scripts.behavior_v3.audit_inputs import near_training
from scripts.behavior_v3.resume_transformer import resume
from scripts.behavior_v3.run_semantic_suffix_control import shuffle_visible_suffix


def test_order_control_preserves_the_visible_multiset_and_terminal_slot():
    row = {'row_id':'fixture', 'actions':[[x] for x in range(300)], 'times':list(range(300))}
    result = shuffle_visible_suffix([row])[0]
    assert Counter(map(tuple,result['actions'])) == Counter(map(tuple,row['actions'][-256:]))
    assert result['actions'][-1] == [299]
    assert result['times'] == [0.] * 256
    assert len(row['actions']) == 300


def test_prefix_filter_matches_brute_force():
    rng = np.random.default_rng(8)
    train = [frozenset(rng.choice(30,20,replace=False)) for _ in range(35)]
    dev = [x-{min(x)} for x in train] + [frozenset({99}), frozenset({1,2,3})]
    assert near_training(train,dev).tolist() == [any(10*len(a&b)>=9*len(a|b) for a in train) for b in dev]


def test_resume_matches_uninterrupted_transformer(tmp_path):
    rows = [{'actions':[[i],[i+1]], 'times':[10,20]} for i in range(8)]
    y = np.array([0,1]*4)
    partial, continuation = tmp_path/'partial', tmp_path/'continuation'
    partial.mkdir()
    continuation.mkdir()
    options = {'kind':'transformer','seed':42,'patience':5,'vocab_ids':range(1916)}
    full, _, _, reference = fit_net(rows,y,rows,y,max_epochs=3,**options)
    fit_net(rows,y,rows,y,max_epochs=1,checkpoint_dir=partial,**options)
    resumed, _, _, record = resume(rows,y,rows,y,42,{'epochs':3,'patience':5},partial,continuation)
    assert record['curves'] == reference['curves']
    for name, value in full.state_dict().items():
        assert torch.equal(value,resumed.state_dict()[name])
