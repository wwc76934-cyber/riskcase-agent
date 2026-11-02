"""Same daily ranking/ties as frozen detector; paired daily differences."""
import hashlib
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


class Ranking:
    def __init__(self, metadata, y, budget=10):
        self.meta = metadata.reset_index(drop=True)
        self.y = np.asarray(y)
        if not self.meta['split'].eq('validation').all() or not self.meta.event_time.lt('2019-01-01').all():
            raise ValueError('Development-only evaluator')
        if len(self.meta) != len(y) or not np.isin(y, [0, 1]).all() or self.meta.transaction_id.duplicated().any():
            raise ValueError('Invalid ranking identities/outcomes')
        self.budget = budget
        self.ids = self.meta.transaction_id.to_numpy(dtype=str)
        self.ties = np.array([hashlib.sha256(f'riskcase-review-ties-v1\0{i}'.encode()).hexdigest() for i in self.ids])
        self.days = self.meta.event_time.str[:10].to_numpy()
        self.groups = {d: np.flatnonzero(self.days == d) for d in sorted(set(self.days))}

    def selected(self, scores):
        scores = np.asarray(scores)
        if scores.shape != self.y.shape or not np.isfinite(scores).all():
            raise ValueError('Finite aligned scores required')
        return np.concatenate([idx[np.lexsort((self.ids[idx], self.ties[idx], scores[idx]))[::-1][:self.budget]] for idx in self.groups.values()])

    def metrics(self, scores, channels=None):
        selected = self.selected(scores)
        mask = np.zeros(len(self.y), dtype=bool); mask[selected] = True
        tp, total, positives = int(self.y[selected].sum()), len(selected), int(self.y.sum())
        result = {'tp_daily10': tp, 'reviewed': total, 'positives': positives,
                  'precision': tp / total if total else None, 'recall': tp / positives if positives else None,
                  'false_positives': total - tp, 'false_negatives': positives - tp,
                  'f1_at_daily10': 2 * tp / (total + positives) if total + positives else None,
                  'ap': float(average_precision_score(self.y, scores)) if positives else None,
                  'auroc': float(roc_auc_score(self.y, scores)) if len(np.unique(self.y)) == 2 else None}
        result['monthly'] = self._groups(self.meta.event_time.str[:7].to_numpy(), mask)
        if channels is not None:
            result['channel'] = self._groups(np.asarray(channels), mask)
        return result

    def _groups(self, values, selected):
        result = {}
        for value in sorted(set(values)):
            mask = values == value
            tp, slots, positives = int(self.y[mask & selected].sum()), int((mask & selected).sum()), int(self.y[mask].sum())
            result[str(value)] = {'rows': int(mask.sum()), 'positives': positives, 'reviewed': slots, 'tp': tp,
                                  'precision': tp / slots if slots else None, 'recall': tp / positives if positives else None}
        return result

    def compare(self, baseline, candidate, repetitions=2000):
        old, new = set(self.selected(baseline)), set(self.selected(candidate))
        added, removed = new - old, old - new
        daily = np.array([sum(int(self.y[i]) for i in added if self.days[i] == day) -
                          sum(int(self.y[i]) for i in removed if self.days[i] == day) for day in self.groups])
        draws = np.random.default_rng(908).choice(daily, size=(repetitions, len(daily)), replace=True).sum(1)
        return {'added_positive': sum(int(self.y[i]) for i in added), 'removed_positive': sum(int(self.y[i]) for i in removed),
                'net': int(daily.sum()), 'changed_slots': len(added), 'paired_day_bootstrap_95': np.quantile(draws, [.025, .975]).tolist(),
                'daily_net': dict(zip(self.groups, daily.tolist())), 'bootstrap_seed': 908, 'repetitions': repetitions,
                'limit': 'Pointwise descriptive development interval; serial dependence/multiple search not resolved'}
