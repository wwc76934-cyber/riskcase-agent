import numpy as np
import torch

from scripts.behavior_v4.run_study import Adapter, SIZE, count_bands, fit_one, make_batch, transitions


def test_pool_preserves_terminal_and_normalizes_nonempty_bands():
    counts = count_bands([np.array([2,2,3])])
    assert counts[:,[2,3]].toarray().tolist() == [[0,1],[1,0],[0,0],[0,0]]


def test_frozen_adapter_updates_projection_and_replays(tmp_path):
    torch.set_num_threads(2)
    seq = [np.array([2+i,3+i]) for i in range(8)]
    counts = count_bands(seq)
    table = np.random.default_rng(0).normal(size=(SIZE,128)).astype(np.float32)
    values = np.zeros((8,0),np.float32)
    y = np.array([0,1]*4)
    _, result = fit_one('random128',42,{'epochs':2,'patience':1,'batch':4},{'random128':table},None,
                        (counts,counts),(values,values),(y,y),tmp_path)
    torch.manual_seed(42)
    initial = Adapter(table,False,0)
    state = torch.load(tmp_path/'model.pt',weights_only=True)
    assert torch.equal(state['table'],initial.table)
    assert not torch.equal(state['projection.weight'],initial.projection.weight)
    assert result['epochs'] == 2
    assert make_batch(counts,np.array([0]))[1].numel() == 5


def test_next_event_counts_are_directional():
    values = transitions([np.array([2,3,2]),np.array([2,3])])
    assert values[2,3] == 2
    assert values[3,2] == 1
    assert values.sum() == 3
