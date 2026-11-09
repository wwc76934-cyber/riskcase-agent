"""Resume the interrupted frozen A2 Transformer without rewriting its checkpoint."""
import argparse
import copy
import json
from pathlib import Path
import random
import shutil
import time

import joblib
import numpy as np
from sklearn.metrics import average_precision_score
import torch
from torch import nn

from app.risk.behavior_semantic.baselines import BehaviorNet, batch, fit_net, prepare, score_net
from app.risk.behavior_semantic.study import development_threshold, metrics, read_records, sha, write


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def schedule(rng, sequences):
    order = rng.permutation(len(sequences))
    batches = []
    for start in range(0, len(sequences), 4096):
        pool = sorted(order[start:start + 4096], key=lambda i: len(sequences[i]))
        batches.extend(np.asarray(pool[j:j + 256]) for j in range(0, len(pool), 256))
    rng.shuffle(batches)
    return batches


def resume(train, y, dev, dy, seed, policy, partial, folder, fallback=None):
    curves = read(partial / 'curves.json')
    epoch = len(curves)
    source = partial / f'epoch-{epoch:02d}.pt'
    best_index = max(range(epoch), key=lambda i: curves[i]['development_AP'])
    best_source = partial / f'epoch-{best_index + 1:02d}.pt'
    if not best_source.is_file() and fallback is not None:
        best_source = fallback / best_source.name
    source_hashes = {str(p): sha(p) for p in {source, best_source, partial / 'curves.json'}}
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(2)
    lookup = {i: i + 2 for i in range(1916)}
    model = BehaviorNet(1918, 'transformer')
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    checkpoint = torch.load(source, weights_only=True, map_location='cpu')
    if checkpoint['epoch'] != epoch or checkpoint['seed'] != seed:
        raise ValueError('Checkpoint epoch/seed mismatch')
    model.load_state_dict(checkpoint['model'])
    optimizer.load_state_dict(checkpoint['optimizer'])
    torch.set_rng_state(checkpoint['torch_rng'])
    best_state = torch.load(best_source, weights_only=True, map_location='cpu')['model']
    best = curves[best_index]['development_AP']
    stale = epoch - best_index - 1
    sequences, times = prepare(train, lookup)
    ds, dt = prepare(dev, lookup)
    n = np.zeros((len(train), 0), np.float32)
    dn = np.zeros((len(dev), 0), np.float32)
    reloaded = average_precision_score(dy, score_net(model, ds, dt, dn))
    if abs(reloaded - curves[-1]['development_AP']) > 1e-12:
        raise ValueError('Interrupted checkpoint does not reproduce logged development AP')
    print(f'transformer seed{seed} reloaded epoch{epoch} AP={reloaded:.6f}',flush=True)
    rng = np.random.default_rng(seed)
    for _ in range(epoch):
        schedule(rng, sequences)
    target = torch.as_tensor(y, dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(20.))
    tick, cpu = time.monotonic(), time.process_time()
    for current in range(epoch + 1, policy['epochs'] + 1):
        if stale >= policy['patience']:
            break
        model.train()
        total = 0.
        for idx in schedule(rng, sequences):
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(*batch(sequences, times, idx, n)), target[idx])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
            total += loss.item() * len(idx)
        ap = float(average_precision_score(dy, score_net(model, ds, dt, dn)))
        curves.append({'epoch': current, 'loss': total / len(train), 'development_AP': ap})
        torch.save({'epoch': current, 'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                    'torch_rng': torch.get_rng_state(), 'seed': seed}, folder / f'epoch-{current:02d}.pt')
        write(folder / 'curves.json', curves)
        print(f'transformer seed{seed} epoch{current} AP={ap:.6f}', flush=True)
        if ap > best:
            best, best_state, stale = ap, copy.deepcopy(model.state_dict()), 0
        else:
            stale += 1
    model.load_state_dict(best_state)
    parameters = sum(p.numel() for p in model.parameters())
    record = {'curves': curves, 'best_development_AP': best, 'parameters': parameters,
              'trainable_parameters': parameters, 'seed': seed, 'test_seen_by_trainer': False,
              'resumed_after_epoch': epoch, 'resumed_checkpoint_sha256': source_hashes,
              'train_wall_seconds': time.monotonic() - tick, 'train_CPU_seconds': time.process_time() - cpu,
              'timing_scope': 'continuation only; initial fragment CPU cost unavailable'}
    return model, lookup, None, record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--partial', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path, action='append', default=[])
    args = parser.parse_args()
    policy = read(args.protocol)
    if any(read(run/'protocol.json') != policy for run in args.previous):
        raise ValueError('Previous partial attempt used different settings')
    if policy['release_final_test'] or policy['models'] != ['transformer'] or policy['seeds'] != [42, 43, 44]:
        raise ValueError('Frozen Transformer-only development protocol required')
    for path, digest in policy['code_sha256'].items():
        if sha(path) != digest:
            raise ValueError('Frozen source changed: ' + path)
    for name in ('records', 'split'):
        if sha(policy[name]) != policy[name + '_sha256']:
            raise ValueError('Frozen input changed: ' + name)
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'protocol.json', policy)
    rows, _ = read_records(policy['records'])
    with np.load(policy['split'], allow_pickle=False) as split:
        train, dev = ([rows[int(i)] for i in split[name]] for name in ('train', 'development'))
    y, dy = (np.array([r['is_fraud'] for r in subset]) for subset in (train, dev))
    artifacts, records, scores = {}, {}, []
    for seed in policy['seeds']:
        key = f'transformer/seed-{seed}'
        folder = out / key
        folder.mkdir(parents=True)
        previous = [root/key for root in reversed(args.previous)]
        complete = next((path for path in previous if (path/'training.json').is_file()),None)
        if complete is not None:
            record = read(complete/'training.json')
            if record['test_seen_by_trainer'] or record['seed'] != seed:
                raise ValueError('Previous completed attempt did not match ' + key)
            for name in ('training.json','model.pt','preparation.joblib','development-scores.npy'):
                shutil.copy2(complete/name,folder/name)
            score = np.load(folder/'development-scores.npy',allow_pickle=False)
            artifacts[key] = {'kind':'torch','folder':str(folder),'reused_from':str(complete)}
            records[key] = record
            scores.append(score)
            print('reused completed checkpoint',key,flush=True)
            continue
        partial = next((path for path in previous if (path/'curves.json').is_file()),None)
        fallback = args.partial if seed == 42 else None
        if partial is not None or fallback is not None:
            model, lookup, prep, record = resume(train,y,dev,dy,seed,policy,partial or fallback,folder,fallback=fallback)
        else:
            model, lookup, prep, record = fit_net(train, y, dev, dy, kind='transformer', seed=seed,
                max_epochs=policy['epochs'], patience=policy['patience'], vocab_ids=range(1916), checkpoint_dir=folder)
        torch.save({'kind': 'transformer', 'vocab': len(lookup) + 2, 'state_dict': model.state_dict()}, folder / 'model.pt')
        joblib.dump({'lookup': lookup, 'prep': prep}, folder / 'preparation.joblib')
        ds, dt = prepare(dev, lookup)
        score = score_net(model, ds, dt, np.zeros((len(dev), 0), np.float32))
        np.save(folder / 'development-scores.npy', score)
        write(folder / 'training.json', record)
        artifacts[key] = {'kind': 'torch', 'folder': str(folder)}
        records[key] = record
        scores.append(score)
    ensemble = np.mean(scores, axis=0)
    np.save(out / 'transformer/ensemble-development.npy', ensemble)
    write(out / 'all-training-complete.json', {'instances': len(artifacts), 'artifacts': artifacts})
    write(out / 'development-results.json', {'status': 'A2_development_comparison_complete_test_still_sealed',
        'records': records, 'development_ensembles': {'transformer': metrics(dy, ensemble, development_threshold(dy, ensemble))},
        'final_test_predictions_generated': False})


if __name__ == '__main__':
    main()
