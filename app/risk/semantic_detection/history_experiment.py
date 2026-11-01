"""Small causal history comparison, separated from the semantic-combination gate."""
from dataclasses import asdict
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from .common import digest, fingerprint, peak_working_set_bytes, write_json
from .data import load_data
from .evaluation import Ranking
from .model import Detector, DetectorConfig, Predictor, Preprocessor, load_model
from .semantics import load_cache
from .temporal import historical_neighbors


def gather_history(tensors, neighbors, ages, positions):
    index = torch.from_numpy(neighbors[positions])
    mask = index >= 0
    if (torch.from_numpy(ages[positions])[mask] <= 0).any():
        raise ValueError('Strictly past observations required')
    safe = index.clamp_min(0)
    # Fill absent slots BEFORE model invocation; no real future row is passed masked.
    values = [torch.where(mask.unsqueeze(-1), t[safe], torch.zeros((), dtype=t.dtype)) for t in tensors[:2]]
    pairs = torch.where(mask, tensors[2][safe], torch.zeros((), dtype=torch.long))
    return (*values, pairs, mask, torch.from_numpy(ages[positions]))


def predict_history(predictor, context_frame, context_meta, target_positions, *, k=6, neighborhood=None):
    neighbors, ages = historical_neighbors(context_meta, k=k) if neighborhood is None else neighborhood
    tensors = predictor.prep.transform(context_frame)
    target_positions = np.asarray(target_positions, dtype=np.int64)
    scores = []; predictor.model.eval()
    with torch.inference_mode():
        for start in range(0, len(target_positions), 2048):
            pos = target_positions[start:start + 2048]
            history = gather_history(tensors, neighbors, ages, pos)
            logits = predictor.model(*(t[pos] for t in tensors), history=history)
            if not torch.isfinite(logits).all():
                raise ValueError('Nonfinite historical prediction')
            scores.append(logits.sigmoid().numpy())
    return np.concatenate(scores)


def run(source_root, cache_path, policy_path, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    policy = json.loads(Path(policy_path).read_text(encoding='utf-8'))
    write_json(output / 'protocol.json', {'policy': policy, 'policy_sha256': digest(policy_path),
               'code': fingerprint(Path(__file__).parent), 'paid_calls': 0, 'final_2019_read': False})
    (X, y, meta), (VX, vy, vmeta) = (loaded := load_data(source_root))[0]
    write_json(output / 'data.json', loaded[1])
    cache, table = load_cache(cache_path)
    write_json(output / 'cache.json', cache)
    begin = time.monotonic(); neighbors, ages = historical_neighbors(meta, k=policy['neighbors'], training=True)
    graph = {'training_nodes': len(meta), 'edges': int((neighbors >= 0).sum()), 'k': policy['neighbors'],
             'build_seconds': time.monotonic() - begin, 'neighbor_arrays_bytes': neighbors.nbytes + ages.nbytes,
             'training_evaluation_nodes': 0, 'zero_history_nodes': int((neighbors < 0).all(1).sum())}
    np.save(output / 'train-neighbors.npy', neighbors); np.save(output / 'train-ages.npy', ages)
    write_json(output / 'graph.json', graph)
    context = pd.concat([X, VX], ignore_index=True); context_meta = pd.concat([meta, vmeta], ignore_index=True)
    # Host-only inference sampler. It is NEVER passed to the training forward.
    begin = time.monotonic()
    inference_neighbors = historical_neighbors(context_meta, k=policy['neighbors'])
    graph['inference_sampler_build_seconds'] = time.monotonic() - begin
    write_json(output / 'graph.json', graph)
    target_positions = np.arange(len(X), len(context), dtype=np.int64)
    ranking = Ranking(vmeta, vy); results = {}; overall = time.monotonic()
    try:
        for mode in policy['modes']:
            seeds, predictions = [], []
            for seed in policy['seeds']:
                directory = output / mode / f'seed-{seed}'; directory.mkdir(parents=True)
                seed_training(seed, 4)
                config = DetectorConfig(variant='B1', history=True, history_mode=mode, **policy['config'])
                prep = Preprocessor().fit(X, cache['keys']); tensors = prep.transform(X)
                target = torch.from_numpy(y.astype(np.float32))
                model = Detector(config, table, prep.numeric.categorical_sizes)
                predictor = Predictor(config, prep, model, list(table.shape))
                optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
                criterion = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(float(config.positive_weight)))
                generator = torch.Generator().manual_seed(seed); curves = []; started = time.monotonic()
                with threadpool_limits(limits=4):
                    for epoch in range(1, policy['epochs'] + 1):
                        model.train(); order = torch.randperm(len(X), generator=generator); loss_sum = 0.; batches = 0
                        for start in range(0, len(X), config.batch_size):
                            pos = order[start:start + config.batch_size]
                            history = gather_history(tensors, neighbors, ages, pos.numpy())
                            optimizer.zero_grad(set_to_none=True)
                            logits = model(*(t[pos] for t in tensors), history=history)
                            loss = criterion(logits, target[pos])
                            if not torch.isfinite(loss):
                                raise ValueError('Nonfinite history loss')
                            loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 5., error_if_nonfinite=True)
                            optimizer.step(); loss_sum += float(loss.detach()) * len(pos); batches += 1
                        row = {'epoch': epoch, 'loss': loss_sum / len(X), 'rows': len(X), 'batches': batches,
                               'elapsed_seconds': time.monotonic() - started}
                        curves.append(row); write_json(directory / 'curves.json', curves)
                        print(f'history-{mode} seed{seed} epoch{epoch} loss={row["loss"]:.6f} seconds={row["elapsed_seconds"]:.1f}', flush=True)
                    training_seconds = time.monotonic() - started
                    predictor.save(directory / 'model')
                    started = time.monotonic()
                    scores = predict_history(predictor, context, context_meta, target_positions, k=policy['neighbors'],
                                              neighborhood=inference_neighbors)
                    inference_seconds = time.monotonic() - started
                    sample = np.random.default_rng(997).choice(len(VX), 32, replace=False)
                    global_targets = target_positions[sample]
                    # Independent small replay includes each target's complete K-neighbor closure.
                    closure = sorted(set(global_targets) | {int(i) for i in inference_neighbors[0][global_targets].flat if i >= 0})
                    mapping = {position: local for local, position in enumerate(closure)}
                    local_targets = [mapping[int(i)] for i in global_targets]
                    reload_scores = predict_history(load_model(directory / 'model'), context.iloc[closure].reset_index(drop=True),
                                                     context_meta.iloc[closure].reset_index(drop=True), local_targets, k=policy['neighbors'])
                    difference = float(np.max(np.abs(scores[sample] - reload_scores)))
                    if difference > 1e-6:
                        raise ValueError('History reload differs')
                np.save(directory / 'development-scores.npy', scores)
                record = {'seed': seed, 'config': asdict(config), 'metrics': ranking.metrics(scores, VX.channel.to_numpy()),
                          'train_seconds': training_seconds, 'inference_seconds': inference_seconds,
                          'reload_max_abs_diff': difference, 'parameters': sum(p.numel() for p in model.parameters()),
                          'peak_process_bytes': peak_working_set_bytes(), 'curves': curves}
                write_json(directory / 'result.json', record); seeds.append(record); predictions.append(scores)
            ensemble = np.mean(predictions, axis=0)
            np.save(output / mode / 'ensemble.npy', ensemble)
            results[mode] = {'seeds': seeds, 'ensemble': ranking.metrics(ensemble, VX.channel.to_numpy())}
            write_json(output / mode / 'summary.json', results[mode])
        report = {'status': 'E2_history_bounded_complete', 'scope': '2018 observed development', 'graph': graph,
                  'results': results, 'final_2019_read': False, 'paid_calls': 0, 'limits': policy['limits'],
                  'elapsed_seconds': time.monotonic() - overall, 'artifacts': fingerprint(output)}
        write_json(output / 'report.json', report)
        return report
    except Exception as exc:
        write_json(output / 'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
