"""Deterministic balanced case gallery; observed history is not a causal explanation."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.risk.semantic_detection.common import digest, write_json
from app.risk.semantic_detection.evaluation import Ranking
from .history import STAT_NAMES
from .history_data import load_with_cards


def alias(kind, value):
    return kind+'-'+hashlib.sha256(('v21-history-case:'+kind+':'+str(value)).encode()).hexdigest()[:12]


def run(source_root, history_run, output):
    history_run, output = Path(history_run), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output/'protocol.json', {'selection': 'First6 by fixed review-tie hash within each prespecified comparison/outcome group',
        'candidate': 'Registered MeanK6 three-seed ensemble', 'baselines': ['frozen_residual', 'mean-self', 'flat', 'statistics'],
        'groups': ['added_positive', 'displaced_positive', 'added_evaluation_negative'],
        'new_training': False, 'candidate_selection': False, 'causal_explanation': False})
    loaded, provenance, _ = load_with_cards(source_root)
    (X, _, meta), (VX, vy, vmeta) = loaded
    context = pd.concat([X, VX], ignore_index=True)
    identities = pd.concat([meta, vmeta], ignore_index=True)
    scores = {'mean': np.load(history_run/'mean/ensemble.npy')}
    files = {'mean': history_run/'mean/ensemble.npy'}
    for name in ('mean-self', 'flat', 'statistics'):
        files[name] = history_run/name/'ensemble.npy'; scores[name] = np.load(files[name])
    files['frozen_residual'] = Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy'
    scores['frozen_residual'] = np.load(files['frozen_residual'])
    ranking = Ranking(vmeta, vy)
    selected = {name: set(ranking.selected(value)) for name,value in scores.items()}
    neighbors = np.load(history_run/'inference-neighbors24.npy', mmap_mode='r')
    stats = np.load(history_run/'inference-statistics.npy', mmap_mode='r')
    days = ranking.groups
    cases, summaries, private_ids = [], {}, {}
    for baseline in ('frozen_residual', 'mean-self', 'flat', 'statistics'):
        added, removed = selected['mean']-selected[baseline], selected[baseline]-selected['mean']
        groups = {'added_positive': [i for i in added if vy[i] == 1],
                  'displaced_positive': [i for i in removed if vy[i] == 1],
                  'added_evaluation_negative': [i for i in added if vy[i] == 0]}
        summaries[baseline] = {'added_positive': len(groups['added_positive']), 'displaced_positive': len(groups['displaced_positive']),
                               'net': len(groups['added_positive'])-len(groups['displaced_positive'])}
        for group, indices in groups.items():
            chosen = sorted(indices, key=lambda i: ranking.ties[i])[:6]
            for i in chosen:
                target = len(X)+i; row = identities.iloc[target]
                previous = neighbors[target, :6]; previous = previous[previous >= 0]
                if any(identities.iloc[j].event_time >= row.event_time or identities.iloc[j].user_id != row.user_id or identities.iloc[j].card_id != row.card_id for j in previous):
                    raise ValueError('Selected case has noncausal or wrong-identity neighbor')
                transaction_alias = alias('tx', row.transaction_id)
                private_ids[transaction_alias] = str(row.transaction_id)
                day_rows = days[str(row.event_time)[:10]]
                ranks = {}
                for name in ('mean', baseline):
                    order = day_rows[np.lexsort((ranking.ids[day_rows], ranking.ties[day_rows], scores[name][day_rows]))[::-1]]
                    ranks[name] = int(np.flatnonzero(order == i)[0])+1
                history = []
                for j in previous:
                    prior = identities.iloc[j]
                    history.append({'transaction_alias': alias('tx', prior.transaction_id), 'source_local_event_time': prior.event_time,
                        'amount_source_cents': float(context.iloc[j].amount_cents), 'MCC': str(context.iloc[j].mcc),
                        'age_seconds': (pd.Timestamp(row.event_time)-pd.Timestamp(prior.event_time)).total_seconds()})
                cases.append({'comparison': 'mean_vs_'+baseline, 'group': group, 'transaction_alias': transaction_alias,
                    'source_local_event_time': row.event_time, 'user_alias': alias('user', row.user_id),
                    'user_card_alias': alias('card', str(row.user_id)+'/'+str(row.card_id)),
                    'evaluation_label': int(vy[i]), 'channel': str(VX.iloc[i].channel), 'MCC': str(VX.iloc[i].mcc),
                    'amount_source_cents': float(VX.iloc[i].amount_cents),
                    'current_original_user_level_count24h': float(VX.iloc[i].count_24h),
                    'candidate_score_uncalibrated': float(scores['mean'][i]), 'baseline_score_uncalibrated': float(scores[baseline][i]),
                    'daily_rank': ranks, 'history_latest_first': history,
                    'fixed14_card_history_statistics': dict(zip(STAT_NAMES, stats[target].tolist()))})
    result = {'status': 'descriptive_history_case_gallery_complete', 'cases': cases, 'comparisons': summaries,
        'data': provenance, 'model_score_sha256': {name:digest(p) for name,p in files.items()},
        'selection': 'Fixed review-tie hash, first6 per outcome stratum; not selected for a persuasive narrative',
        'limit': 'Observed source history is not an attribution of model score. Evaluation positives/negatives from synthetic benchmark, not real investigation verdicts. Original point counts retain user-level scope; new neighbor/statistic scope is user-card.',
        'model_replay_complete': False, 'no_Agent_integration': True, 'final_2019_read': False, 'paid_calls': 0}
    write_json(output/'report.json', result)
    write_json(output/'private-id-map.json', private_ids)
    return result
