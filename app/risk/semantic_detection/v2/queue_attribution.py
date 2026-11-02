"""Exact, path-dependent queue decomposition; no training or quota optimization."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.risk.semantic_detection.common import digest, write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .audit_matched import manual_ap


class Queues:
    def __init__(self, metadata, labels, channels, budget=10):
        self.ranking = Ranking(metadata, labels, budget=budget)
        self.channels = np.asarray(channels)
        if self.channels.shape != self.ranking.y.shape:
            raise ValueError('Aligned channel labels required')
        self.channel_values = sorted(set(self.channels))
        self.groups = [[idx[self.channels[idx] == channel] for channel in self.channel_values]
                       for idx in self.ranking.groups.values()]

    def profile(self, scores):
        r = self.ranking
        selected = r.selected(scores)
        mask = np.zeros(len(r.y), dtype=bool); mask[selected] = True
        shape = (len(self.groups), len(self.channel_values))
        quotas = np.zeros(shape, dtype=np.int16)
        prefix = np.zeros(shape+(r.budget+1,), dtype=np.int16)
        for day, groups in enumerate(self.groups):
            for channel, idx in enumerate(groups):
                quotas[day, channel] = int(mask[idx].sum())
                ordered = idx[np.lexsort((r.ids[idx], r.ties[idx], np.asarray(scores)[idx]))[::-1][:r.budget]]
                counts = np.r_[0, np.cumsum(r.y[ordered])]
                prefix[day, channel, :len(counts)] = counts
                prefix[day, channel, len(counts):] = counts[-1]
        expected = int(r.y[selected].sum())
        actual = int(np.take_along_axis(prefix, quotas[:, :, None], axis=2).sum())
        if expected != actual:
            raise ValueError('Within-channel prefixes do not reconstruct the original daily queue')
        channels = {}
        for channel in self.channel_values:
            group = self.channels == channel
            chosen = group & mask
            channels[str(channel)] = {'rows': int(group.sum()), 'positives': int(r.y[group].sum()),
                'reviewed': int(chosen.sum()), 'TP': int(r.y[chosen].sum()),
                'within_channel_AP': manual_ap(r.y[group], np.asarray(scores)[group]) if r.y[group].sum() else None}
        return {'TP': expected, 'quotas': quotas, 'prefix': prefix, 'channels': channels}

    @staticmethod
    def compare(old, new):
        swap = lambda order, quota: int(np.take_along_axis(order['prefix'], quota['quotas'][:, :, None], axis=2).sum())
        old_new, new_old = swap(old, new), swap(new, old)
        net = new['TP']-old['TP']
        result = {'old_TP': old['TP'], 'new_TP': new['TP'], 'net': net,
                  'old_order_new_quota_TP': old_new, 'new_order_old_quota_TP': new_old,
                  'quota_effect_at_old_order': old_new-old['TP'], 'order_effect_at_new_quota': new['TP']-old_new,
                  'order_effect_at_old_quota': new_old-old['TP'], 'quota_effect_at_new_order': new['TP']-new_old,
                  'interaction': new['TP']-old_new-new_old+old['TP']}
        if result['quota_effect_at_old_order']+result['order_effect_at_new_quota'] != net or result['order_effect_at_old_quota']+result['quota_effect_at_new_order'] != net:
            raise ValueError('Queue decomposition does not sum exactly')
        return result


def run(source_root, research_root, audit, history_run, broad_run, phase, output):
    research_root, audit, output = map(Path, (research_root, audit, output))
    output.mkdir(parents=True, exist_ok=False)
    (_, _, _), (VX, vy, vmeta) = (loaded := load_data(source_root))[0]
    queues = Queues(vmeta, vy, VX.channel.to_numpy())
    files, pairs = {}, []
    if phase == 'a':
        for name in ('frozen_residual', 'strong_tree', 'B0', 'B1', 'B2', 'B3', 'B3_shuffled', 'mean', 'attention'):
            files[name] = audit/f'{name}-development.npy'
        pairs = [(name, 'frozen_residual') for name in files if name != 'frozen_residual']
        for representation in ('original', 'learned', 'semantic'):
            for use_channel in (False, True):
                key = ('channel' if use_channel else 'nochannel')+'-'+representation
                files[key] = research_root/'runs/semantic-v21-a2-01'/(key+'-32')/'ensemble.npy'
            pairs.append(('channel-'+representation, 'nochannel-'+representation))
        pairs.extend([('nochannel-semantic', 'nochannel-learned'), ('channel-semantic', 'channel-learned')])
    elif phase == 'b':
        history_run, broad_run = Path(history_run), Path(broad_run)
        if not (history_run/'report.json').is_file() or not (broad_run/'report.json').is_file():
            raise ValueError('Completed B study and final broad controls required')
        files = {name: history_run/name/'ensemble.npy' for name in ('flat', 'statistics', 'mean', 'decay', 'gru', 'transformer', 'attention')}
        files.update({'frozen_residual': Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy',
                      'broad_neural': broad_run/'neural-ensemble.npy', 'broad_tree': broad_run/'tree-development-scores.npy'})
        pairs = [(mode, baseline) for mode in ('mean', 'decay', 'gru', 'transformer', 'attention')
                 for baseline in ('flat', 'frozen_residual', 'broad_neural', 'broad_tree')]
    else:
        raise ValueError('Declared phase a or b required')
    profiles = {name: queues.profile(np.load(file, allow_pickle=False)) for name, file in files.items()}
    comparisons = {name+'_vs_'+baseline: queues.compare(profiles[baseline], profiles[name]) for name, baseline in pairs}
    records = [{'comparison': name, **value} for name, value in comparisons.items()]
    pd.DataFrame(records).to_csv(output/'decomposition.csv', index=False)
    result = {'status': 'read_only_queue_factorization_complete', 'phase': phase,
              'comparisons': comparisons, 'channel_profiles': {name: value['channels'] for name,value in profiles.items()},
              'model_score_sha256': {name: digest(file) for name, file in files.items()},
              'data': loaded[1], 'budget': 10, 'new_training': False, 'label_based_selection_or_optimization': False,
              'limit': 'Exact queue arithmetic with two reference paths; not causal attribution, a deployable model, or a new candidate selected on evaluation outcomes',
              'final_2019_read': False, 'paid_calls': 0}
    write_json(output/'report.json', result)
    return result
