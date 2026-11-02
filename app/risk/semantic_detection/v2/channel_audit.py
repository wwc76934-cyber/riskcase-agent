"""Reload fixed artifacts; audit observed year/channel ranks without optimizing models."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, fisher_exact, spearmanr
from threadpoolctl import threadpool_limits
import torch

from app.risk.neural_v2 import load_v2
from app.risk.semantic_detection.common import digest, fingerprint, write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from app.risk.semantic_detection.history_experiment import predict_history
from app.risk.semantic_detection.model import load_model


def daily_ranks(ranking, scores):
    """Rank every row, with exactly the frozen descending score/SHA/ID ties."""
    values = np.asarray(scores)
    if values.shape != ranking.y.shape or not np.isfinite(values).all():
        raise ValueError('Finite aligned scores required')
    ranks = np.empty(len(values), dtype=np.int32)
    for positions in ranking.groups.values():
        ordered = positions[np.lexsort((ranking.ids[positions], ranking.ties[positions], values[positions]))[::-1]]
        ranks[ordered] = np.arange(1, len(ordered) + 1)
    return ranks


def population(frame, y, metadata):
    values = pd.DataFrame({'year': metadata.event_time.str[:4].to_numpy(),
                           'channel': frame.channel.to_numpy(), 'positive': y})
    result = values.groupby(['year', 'channel'], observed=True).positive.agg(['size', 'sum']).reset_index()
    result = result.rename(columns={'size': 'rows', 'sum': 'positive'})
    result['negative'] = result.rows - result.positive
    result['positive_rate'] = result.positive / result.rows
    result['transaction_share'] = result.rows / result.groupby('year').rows.transform('sum')
    return result


def drift(counts):
    table = counts.pivot(index='year', columns='channel', values='rows')
    reports = []
    for year in ('2016', '2017'):
        a, b = table.loc[year].to_numpy(), table.loc['2018'].to_numpy()
        statistic, pvalue, dof, _ = chi2_contingency(np.stack([a, b]))
        rates = []
        for channel in table.columns:
            old = counts[(counts.year == year) & (counts.channel == channel)].iloc[0]
            new = counts[(counts.year == '2018') & (counts.channel == channel)].iloc[0]
            odds, p = fisher_exact([[int(old.positive), int(old.negative)], [int(new.positive), int(new.negative)]])
            rates.append({'channel': channel, 'odds_train_year_over_2018': float(odds),
                          'fisher_p_unadjusted': float(p), 'fisher_p_bonferroni6': min(1., float(p) * 6),
                          'train_year_rate': float(old.positive_rate), 'development_rate': float(new.positive_rate)})
        reports.append({'year_vs_2018': year, 'total_variation': float(np.abs(a / a.sum() - b / b.sum()).sum() / 2),
                        'chi_squared': float(statistic), 'p_unadjusted': float(pvalue), 'dof': int(dof),
                        'cramers_v': float(np.sqrt(statistic / (a.sum() + b.sum()))), 'label_rate_tests': rates})
    return reports


def score_groups(frame, scores, y, metadata, *, mcc=False):
    values = pd.DataFrame({'year': metadata.event_time.str[:4].to_numpy(), 'channel': frame.channel.to_numpy(),
                           'mcc': frame.mcc.astype(str).to_numpy(), 'score': scores, 'positive': y})
    names = ['year', 'channel', 'mcc'] if mcc else ['year', 'channel']
    groups = values.groupby(names, dropna=False, observed=True)
    result = groups.agg(rows=('score', 'size'), positives=('positive', 'sum'), mean_score=('score', 'mean'))
    for q in (.01, .1, .5, .9, .95, .99, .999, 1.):
        result[f'q{q:g}'] = groups.score.quantile(q)
    return result.reset_index()


def _alias(identity):
    return hashlib.sha256(('riskcase-v21-audit-example\0' + identity).encode()).hexdigest()[:16]


def rank_changes(ranking, ranks, channels, *, baseline, candidate):
    a, b = ranks[baseline], ranks[candidate]
    result = []
    for channel in sorted(set(channels)):
        mask = channels == channel
        old = (a <= 10) & mask
        new = (b <= 10) & mask
        pos = mask & (ranking.y == 1)
        result.append({'baseline': baseline, 'candidate': candidate, 'channel': str(channel),
                       'rows': int(mask.sum()), 'mean_rank_delta': float(np.mean(b[mask] - a[mask])),
                       'spearman_day_rank': float(spearmanr(a[mask], b[mask]).statistic),
                       'old_slots': int(old.sum()), 'new_slots': int(new.sum()),
                       'new_positive': int(ranking.y[new & ~old].sum()),
                       'displaced_positive': int(ranking.y[old & ~new].sum()),
                       'positive_old_median_rank': float(np.median(a[pos])) if pos.any() else None,
                       'positive_new_median_rank': float(np.median(b[pos])) if pos.any() else None})
    return result


def failure_examples(ranking, scores, ranks, frame, baseline='frozen_residual', candidate='B2', limit=12):
    old, new = ranks[baseline] <= 10, ranks[candidate] <= 10
    examples = []
    for day, positions in ranking.groups.items():
        displaced = positions[old[positions] & ~new[positions] & (ranking.y[positions] == 1)]
        admitted = positions[new[positions] & ~old[positions] & (ranking.y[positions] == 0)
                             & frame.channel.iloc[positions].eq('Online Transaction').to_numpy()]
        if len(displaced) and len(admitted):
            def record(i):
                return {'alias': _alias(ranking.ids[i]), 'channel': str(frame.channel.iloc[i]), 'mcc': str(frame.mcc.iloc[i]),
                        'label_for_posthoc_audit_only': int(ranking.y[i]),
                        'ranks': {name: int(values[i]) for name, values in ranks.items()},
                        'scores': {name: float(values[i]) for name, values in scores.items()}}
            examples.append({'day': day, 'displaced_positive': record(int(displaced[0])),
                             'admitted_online_negative': record(int(admitted[0])),
                             'interpretation': 'Same daily capacity competition, not an individual causal feature effect'})
        if len(examples) >= limit:
            break
    return examples


def _predict_existing(name, root, source, train, validation, train_meta, validation_meta):
    """Only local trusted joblib/weights. No state_dict update or optimizer exists here."""
    source_paths, configs, training_predictions, validation_predictions = [], [], [], []
    for seed in (42, 43, 44) if name != 'strong_tree' else (42,):
        if name in ('B0', 'B1', 'B2', 'B3', 'B3_shuffled'):
            directory = root / 'runs/semantic-e1-additive-v2' / name / f'seed-{seed}'
            predictor = load_model(directory / 'model')
            a = predictor.predict(train); b = predictor.predict(validation)
            configs.append(asdict(predictor.config))
        elif name == 'frozen_residual':
            directory = source / 'runs/neural-v2-development-01' / f'confirmation-seed-{seed}'
            predictor = load_v2(directory / 'model')
            a = predictor.predict(train); b = predictor.predict(validation)
            configs.append(asdict(predictor.config))
        elif name == 'strong_tree':
            directory = source / 'runs/hgb-raw-nochannel-v2-development-01'
            model = joblib.load(directory / 'model.joblib')
            a = model.predict_proba(train)[:, 1]; b = model.predict_proba(validation)[:, 1]
            configs.append({'artifact': 'Trusted frozen strong tree; inspect protocol for original config'})
        else:
            from app.risk.semantic_detection.temporal import historical_neighbors
            directory = root / 'runs/semantic-e2-history-v1' / name / f'seed-{seed}'
            predictor = load_model(directory / 'model')
            configs.append(asdict(predictor.config))
            run = root / 'runs/semantic-e2-history-v1'
            neighborhood = (np.load(run / 'train-neighbors.npy'), np.load(run / 'train-ages.npy'))
            a = predict_history(predictor, train, train_meta, np.arange(len(train)), neighborhood=neighborhood)
            context = pd.concat([train, validation], ignore_index=True)
            meta = pd.concat([train_meta, validation_meta], ignore_index=True)
            if not hasattr(_predict_existing, '_inference_neighbors'):
                _predict_existing._inference_neighbors = historical_neighbors(meta, k=6)
            b = predict_history(predictor, context, meta, np.arange(len(train), len(context)),
                                neighborhood=_predict_existing._inference_neighbors)
        expected = np.load(directory / 'development-scores.npy', allow_pickle=False)
        difference = float(np.max(np.abs(expected - b)))
        if difference > 1e-6:
            raise ValueError(f'{name} seed {seed} original development score mismatch: {difference}')
        source_paths.append({'directory': str(directory), 'artifacts': fingerprint(directory / 'model') if name != 'strong_tree'
                             else {'model.joblib': digest(directory / 'model.joblib')},
                             'development_replay_max_abs_diff': difference})
        training_predictions.append(a); validation_predictions.append(b)
        print(f'channel-audit {name} seed{seed} replay_diff={difference:g}', flush=True)
    return np.mean(training_predictions, axis=0), np.mean(validation_predictions, axis=0), {'sources': source_paths, 'configs': configs}


def run(source_root, research_root, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    write_json(output / 'protocol.json', {'mode': 'posthoc channel attribution, no new training',
               'code': fingerprint(Path(__file__).parent), 'evaluation': '2018 observed development', 'final_2019_read': False})
    try:
        (X, y, meta), (VX, vy, vmeta) = (loaded := load_data(source_root))[0]
        write_json(output / 'data.json', loaded[1])
        allX = pd.concat([X, VX], ignore_index=True); ally = np.concatenate([y, vy]); allmeta = pd.concat([meta, vmeta], ignore_index=True)
        counts = population(allX, ally, allmeta); counts.to_csv(output / 'channel-population.csv', index=False)
        ranking = Ranking(vmeta, vy); scores, ranks, models, channel_tables, mcc_tables = {}, {}, {}, [], []
        names = ('frozen_residual', 'strong_tree', 'B0', 'B1', 'B2', 'B3', 'B3_shuffled', 'mean', 'attention')
        torch.set_num_threads(4)
        for name in names:
            begin = time.monotonic()
            with threadpool_limits(limits=4):
                train_scores, dev_scores, record = _predict_existing(name, Path(research_root), Path(source_root), X, VX, meta, vmeta)
            np.save(output / f'{name}-train.npy', train_scores); np.save(output / f'{name}-development.npy', dev_scores)
            scores[name] = dev_scores; ranks[name] = daily_ranks(ranking, dev_scores)
            values = np.concatenate([train_scores, dev_scores])
            c = score_groups(allX, values, ally, allmeta); c.insert(0, 'model', name); channel_tables.append(c)
            m = score_groups(allX, values, ally, allmeta, mcc=True); m.insert(0, 'model', name); mcc_tables.append(m)
            record.update(metrics=ranking.metrics(dev_scores, VX.channel.to_numpy()), elapsed_seconds=time.monotonic() - begin)
            models[name] = record
            write_json(output / f'{name}.json', record)
            print(f'channel-audit {name} TP={record["metrics"]["tp_daily10"]}', flush=True)
        pd.concat(channel_tables).to_csv(output / 'channel-scores.csv', index=False)
        pd.concat(mcc_tables).to_csv(output / 'mcc-channel-scores.csv', index=False)
        transitions = [r for candidate in ('B1', 'B2', 'B3', 'mean', 'attention')
                       for baseline in ('frozen_residual', 'B1') if baseline != candidate
                       for r in rank_changes(ranking, ranks, VX.channel.to_numpy(), baseline=baseline, candidate=candidate)]
        pd.DataFrame(transitions).to_csv(output / 'rank-changes.csv', index=False)
        np.savez_compressed(output / 'development-daily-ranks.npz', **ranks)
        examples = failure_examples(ranking, scores, ranks, VX)
        comparisons = {name: ranking.compare(scores['frozen_residual'], scores[name]) for name in names if name != 'frozen_residual'}
        report = {'status': 'A1_complete_no_new_training', 'models': models, 'population': counts.to_dict('records'),
                  'distribution_drift': drift(counts), 'rank_changes': transitions, 'failure_examples': examples,
                  'comparisons_vs_frozen': comparisons,
                  'alignment': {'training_ids_sha256': hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest(),
                                'development_ids_sha256': hashlib.sha256('\n'.join(vmeta.transaction_id).encode()).hexdigest()},
                  'elapsed_seconds': time.monotonic() - started, 'paid_calls': 0, 'final_2019_read': False,
                  'limits': ['Training scores are in-sample, not generalization estimates', 'Year/channel drift is association, not a semantic causal attribution',
                             'Original architecture/category widths differ from E1; A2 is required', '2018 repeatedly observed development data']}
        write_json(output / 'report.json', report)
        return report
    except Exception as exc:
        write_json(output / 'failure.json', {'type': type(exc).__name__, 'message': str(exc), 'elapsed_seconds': time.monotonic() - started})
        raise
