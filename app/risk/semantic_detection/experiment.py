"""Immutable research run: fixed E1 matrix, curves, scores, paired analysis and reload."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time

import numpy as np
import torch
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from .common import digest, fingerprint, write_json, peak_working_set_bytes
from .data import audit_fields, load_data
from .evaluation import Ranking
from .model import Detector, DetectorConfig, Predictor, Preprocessor, load_model
from .semantics import build_cache, category_key, load_cache
from .losses import class_balanced_weights, consistency_loss, supervised_loss


def prepare(source_root, glossary_path, model_path, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    (X, y, meta), = (loaded := load_data(source_root, train_only=True))[0]
    glossary = json.loads(Path(glossary_path).read_text(encoding='utf-8'))
    report = audit_fields(X, meta, glossary)
    report.update({'provenance': loaded[1], 'train_positives': int(y.sum()), 'model_path': str(Path(model_path).resolve()),
                   'glossary': glossary['source'], 'final_2019_read': False})
    write_json(output / 'field-audit.json', report)
    manifest = build_cache({category_key(m, c) for m, c in zip(X.mcc, X.channel)}, glossary_path, model_path,
                           output / 'embedding-cache')
    write_json(output / 'report.json', {'status': 'prepared', 'audit': report, 'cache': manifest})
    return report


def fit(config, X, y, keys, table, *, seed, epochs, output, ranking=None, validation=None, channels=None,
        loss_kind='bce', consistency_weight=0.):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    if not 1 <= epochs <= 50:
        raise ValueError('Bounded positive training epochs required')
    seed_training(seed, 4)
    prep = Preprocessor().fit(X, keys)
    tensors = prep.transform(X)
    target = torch.from_numpy(np.asarray(y).astype(np.float32))
    model = Detector(config, table, prep.numeric.categorical_sizes)
    predictor = Predictor(config, prep, model, list(table.shape))
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    class_weights = torch.tensor(class_balanced_weights(target), dtype=torch.float32) if loss_kind == 'cb_focal' else None
    if loss_kind not in {'bce', 'focal', 'cb_focal'} or not 0 <= consistency_weight <= 1:
        raise ValueError('Supported bounded loss required')
    if not np.isin(y, [-1, 0, 1]).all():
        raise ValueError('Only exposed labels or -1 sentinel allowed')
    generator = torch.Generator().manual_seed(seed)
    curves = []
    started = time.monotonic()
    for epoch in range(1, epochs + 1):
        model.train(); order = torch.randperm(len(target), generator=generator)
        total, batches, max_norm = 0., 0, 0.
        for start in range(0, len(target), config.batch_size):
            pos = order[start:start + config.batch_size]
            optimizer.zero_grad(set_to_none=True)
            logits = model(*(t[pos] for t in tensors))
            labels = target[pos]
            loss = supervised_loss(logits, labels, kind=loss_kind, positive_weight=config.positive_weight,
                                   class_weights=class_weights)
            if consistency_weight and (labels < 0).any():
                # Two independent dropout masks; no feature edits, IDs, graph rewiring or pseudo-labels.
                second = model(*(t[pos] for t in tensors))
                loss = loss + consistency_weight * consistency_loss(logits, second, labels)
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite training loss')
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 5., error_if_nonfinite=True)
            max_norm = max(max_norm, float(norm))
            optimizer.step()
            total += float(loss.detach()) * len(pos); batches += 1
        row = {'epoch': epoch, 'loss': total / len(target), 'rows': len(target), 'batches': batches,
               'max_preclip_gradient_norm': max_norm, 'elapsed_seconds': time.monotonic() - started}
        # Only final epoch is scored; no development-driven early selection.
        curves.append(row)
        with (output / 'curves.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, allow_nan=False) + '\n')
        print(f'{config.variant} seed{seed} epoch{epoch} loss={row["loss"]:.6f} seconds={row["elapsed_seconds"]:.1f}', flush=True)
    train_seconds = time.monotonic() - started
    predictor.save(output / 'model')
    report = {'config': asdict(config), 'seed': seed, 'epochs': epochs, 'train_seconds': train_seconds,
              'loss_kind': loss_kind, 'consistency_weight': consistency_weight,
              'exposed_negative': int((target == 0).sum()), 'exposed_positive': int((target == 1).sum()),
              'unlabeled': int((target < 0).sum()), 'class_weights': class_weights.tolist() if class_weights is not None else None,
              'parameters_total': sum(p.numel() for p in model.parameters()),
              'parameters_trainable': sum(p.numel() for p in model.parameters() if p.requires_grad),
              'model_bytes': sum(p.stat().st_size for p in (output / 'model').iterdir()), 'curves': curves,
              'whole_process_peak_working_set_bytes': peak_working_set_bytes()}
    if validation is not None:
        started = time.monotonic(); scores = predictor.predict(validation)
        report['prediction_seconds'] = time.monotonic() - started
        report['prediction_rows'] = len(scores)
        sample = np.random.default_rng(975).choice(len(validation), min(96, len(validation)), replace=False)
        reloaded = load_model(output / 'model').predict(validation.iloc[sample])
        difference = float(np.max(np.abs(reloaded - scores[sample])))
        if difference > 1e-6:
            raise ValueError('Reload predictions differ')
        report['reload_max_abs_diff'] = difference
        np.save(output / 'development-scores.npy', scores)
        report['metrics'] = ranking.metrics(scores, channels)
    write_json(output / 'result.json', report)
    return predictor, report


def run_e1(source_root, cache_path, protocol_path, output):
    output, source_root = Path(output), Path(source_root)
    output.mkdir(parents=True, exist_ok=False)
    policy = json.loads(Path(protocol_path).read_text(encoding='utf-8'))
    if policy.get('encoding_contract') != 'additive-v2':
        raise ValueError('Formal E1 requires additive-v2 policy; replacement pilot is archived')
    root = Path(__file__).resolve().parents[3]
    write_json(output / 'protocol.json', {'policy': policy, 'created_at': datetime.now(timezone.utc).isoformat(),
               'code_files': fingerprint(root / 'app/risk/semantic_detection'), 'cli_sha256': digest(root / 'app/cli/semantic_detection_research.py'),
               'python': platform.python_version(), 'torch': torch.__version__, 'device': 'cpu', 'threads': 4,
               'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
               'protocol_sha256': digest(protocol_path), 'paid_calls': 0, 'default_detector_changed': False})
    shutil.copyfile(protocol_path, output / 'frozen-policy.json')
    for name, value in fingerprint(root / 'app/risk/semantic_detection').items():
        destination = output / 'frozen-code' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / 'app/risk/semantic_detection' / name, destination)
    started = time.monotonic()
    try:
        (X, y, meta), (VX, vy, vmeta) = (loaded := load_data(source_root))[0]
        write_json(output / 'data.json', loaded[1])
        cache, table = load_cache(cache_path)
        if cache['encoder']['revision'] != policy['encoder']['revision'] or table.shape[1] != policy['dimensions']:
            raise ValueError('Encoder/cache differs from frozen policy')
        write_json(output / 'cache.json', cache)
        ranking = Ranking(vmeta, vy)
        all_results, ensembles = {}, {}
        for variant in policy['variants']:
            reports, scores = [], []
            for seed in policy['seeds']:
                destination = output / variant / f'seed-{seed}'
                with threadpool_limits(limits=4):
                    _, report = fit(DetectorConfig(variant=variant, **policy['config']), X, y, cache['keys'], table,
                                    seed=seed, epochs=policy['epochs'], output=destination,
                                    ranking=ranking, validation=VX, channels=VX.channel.to_numpy())
                reports.append(report)
                scores.append(np.load(destination / 'development-scores.npy', allow_pickle=False))
                print(f'{variant} seed{seed} TP={report["metrics"]["tp_daily10"]} AP={report["metrics"]["ap"]:.6f}', flush=True)
            ensemble = np.mean(scores, axis=0)
            np.save(output / variant / 'ensemble.npy', ensemble)
            ensembles[variant] = ensemble
            all_results[variant] = {'seeds': reports, 'ensemble': ranking.metrics(ensemble, VX.channel.to_numpy())}
            write_json(output / variant / 'summary.json', all_results[variant])
        controls = {}
        for name, file in {'frozen_residual': 'runs/neural-v2-development-01/ensemble-development-scores.npy',
                           'matched_tree_nochannel': 'runs/hgb-raw-nochannel-v2-development-01/development-scores.npy'}.items():
            path = source_root / file
            score = np.load(path, allow_pickle=False)
            controls[name] = {'score_sha256': digest(path), 'metrics': ranking.metrics(score, VX.channel.to_numpy()),
                              'limit': 'Historical frozen controls omit channel; B0-B3 matched controls all include it'}
            ensembles[name] = score
        comparisons = {f'{candidate}_vs_{baseline}': ranking.compare(ensembles[baseline], ensembles[candidate])
                       for candidate in ('B2', 'B3') for baseline in ('B1', 'frozen_residual', 'matched_tree_nochannel')}
        comparisons['B3_vs_B3_shuffled'] = ranking.compare(ensembles['B3_shuffled'], ensembles['B3'])
        admitted = any(comparisons[f'{v}_vs_B1']['paired_day_bootstrap_95'][0] > 0 and
                       (v != 'B3' or comparisons['B3_vs_B3_shuffled']['paired_day_bootstrap_95'][0] > 0)
                       for v in ('B2', 'B3'))
        # Independent existing heap-based reviewer verifies all final lists/metrics once.
        from app.risk.ml_evaluation import evaluate_scores
        independent = {}
        for variant, scores in ensembles.items():
            metrics, queue = evaluate_scores(vmeta, vy, scores)
            expected = ranking.selected(scores)
            if {r['transaction_id'] for r in queue} != set(vmeta.transaction_id.iloc[expected]):
                raise ValueError('Independent daily queue mismatch')
            if metrics['daily_review']['true_positives'] != ranking.metrics(scores)['tp_daily10']:
                raise ValueError('Independent count mismatch')
            independent[variant] = {'rows': len(scores), 'reviewed': len(queue), 'verified': True}
        report = {'status': 'E1_complete', 'evaluation_scope': '2018 observed development', 'final_2019_read': False,
                  'encoding_contract': 'additive-v2',
                  'fit_rows': len(y), 'fit_positives': int(y.sum()), 'development_rows': len(vy), 'development_positives': int(vy.sum()),
                  'results': all_results, 'historical_controls': controls, 'comparisons': comparisons,
                  'semantic_phase2_admitted': admitted, 'independent_ranking': independent,
                  'elapsed_seconds': time.monotonic() - started, 'paid_calls': 0,
                  'limits': ['Synthetic source', 'No new independent future evaluation', 'Modern dictionary/pretraining postdate transactions',
                             'B0 separate field embeddings vs B1 joint embedding; compare B1/B2 for semantics',
                             'B3 extra projection capacity disclosed; shuffled B3 matches it', 'Pointwise bootstrap not search-adjusted'],
                  'artifact_sha256': fingerprint(output)}
        write_json(output / 'report.json', report)
        print(json.dumps({'status': report['status'], 'phase2': admitted,
                          'TP': {v: r['ensemble']['tp_daily10'] for v, r in all_results.items()}}), flush=True)
        return report
    except Exception as exc:
        write_json(output / 'failure.json', {'type': type(exc).__name__, 'message': str(exc),
                                             'elapsed_seconds': time.monotonic() - started})
        raise
