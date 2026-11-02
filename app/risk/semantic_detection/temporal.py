"""Optional bounded one-hop history; no future graph is exposed to the model."""
from collections import defaultdict, deque

import numpy as np
import pandas as pd


def historical_neighbors(metadata, *, k=6, group='user_id', training=False):
    required = {'transaction_id', 'user_id', 'event_time', 'split'}
    if not required <= set(metadata) or not 1 <= k <= 64 or group not in {'user_id', 'user_card'}:
        raise ValueError('Bounded history and required metadata expected')
    if training and not metadata['split'].eq('train').all():
        raise ValueError('Evaluation nodes forbidden in training graph')
    if group == 'user_card' and 'card_id' not in metadata:
        raise ValueError('User-card joint identity required')
    if group == 'user_card' and (metadata.card_id.isna().any() or metadata.card_id.astype(str).str.strip().eq('').any()):
        raise ValueError('Known within-user card identity required')
    if metadata.transaction_id.duplicated().any():
        raise ValueError('Duplicate node identity')
    if metadata.user_id.isna().any() or metadata.user_id.astype(str).str.strip().eq('').any():
        raise ValueError('Known user identity required')
    parsed = pd.to_datetime(metadata.event_time, format='ISO8601', errors='raise')
    if parsed.isna().any() or parsed.dt.tz is not None:
        raise ValueError('Known source-local event time required')
    times = parsed.astype('int64').to_numpy()
    if training and (not parsed.ge(pd.Timestamp('2016-01-01')).all() or not parsed.lt(pd.Timestamp('2018-01-01')).all()):
        raise ValueError('Only 2016-2017 observations allowed in training graph')
    users = metadata.user_id.astype(str).to_numpy()
    keys = [(u, str(c)) for u, c in zip(users, metadata.card_id)] if group == 'user_card' else list(users)
    order = sorted(range(len(metadata)), key=lambda i: (keys[i], times[i], str(metadata.transaction_id.iloc[i])))
    neighbors = np.full((len(metadata), k), -1, dtype=np.int64)
    ages = np.zeros((len(metadata), k), dtype=np.float32)
    history = defaultdict(lambda: deque(maxlen=k))
    pos = 0
    while pos < len(order):
        i = order[pos]; end = pos + 1
        while end < len(order) and keys[order[end]] == keys[i] and times[order[end]] == times[i]:
            end += 1
        tied = order[pos:end]
        for j in tied:
            prior = list(history[keys[j]])[::-1]
            neighbors[j, :len(prior)] = prior
            ages[j, :len(prior)] = [(times[j] - times[p]) / 1e9 for p in prior]
        for j in tied:
            history[keys[j]].append(j)
        pos = end
    return neighbors, ages
