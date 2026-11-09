"""Audit frozen FraudNLP inputs and development-to-training sequence similarity."""
import argparse
from array import array
from collections import Counter, defaultdict
import csv
import itertools
import json
from pathlib import Path

import numpy as np

from app.risk.behavior_semantic.baselines import events, prepare
from app.risk.behavior_semantic.semantics import history_shuffle
from app.risk.behavior_semantic.study import sha, write


def summarize(values):
    values = np.asarray(values)
    return {'count': len(values), 'min_p50_p95_p99_max': np.quantile(values, [0,.5,.95,.99,1]).tolist()}


def shingles(row):
    sequence = events(row)
    if len(sequence) < 3:
        sequence = [*sequence, *([-1] * (3 - len(sequence)))]
    return frozenset(((a+1) << 22) | ((b+1) << 11) | (c+1)
                     for a,b,c in zip(sequence, sequence[1:], sequence[2:]))


def near_training(train, dev):
    """Exact Jaccard >= .9 existence via a lossless globally ordered prefix index."""
    train_sets = list(dict.fromkeys(train))
    frequency = Counter(itertools.chain.from_iterable(train_sets))

    def prefix(grams):
        # A qualifying pair's first shared gram must be in each prefix.
        minimum_overlap = (9 * len(grams) + 9) // 10
        return sorted(grams, key=lambda gram: (frequency[gram], gram))[:len(grams)-minimum_overlap+1]

    postings = defaultdict(list)
    for i, grams in enumerate(train_sets):
        for gram in prefix(grams):
            postings[gram].append(i)
    memo = {}
    for grams in dev:
        if grams in memo:
            continue
        candidates = set(itertools.chain.from_iterable(postings[gram] for gram in prefix(grams)))
        matched = False
        for i in candidates:
            other = train_sets[i]
            if 10 * min(len(other), len(grams)) < 9 * max(len(other), len(grams)):
                continue
            shared = len(other & grams)
            if 10 * shared >= 9 * (len(other) + len(grams) - shared):
                matched = True
                break
        memo[grams] = matched
    return np.array([memo[grams] for grams in dev], dtype=bool)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--notebook', type=Path, required=True)
    parser.add_argument('--dictionary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads(args.protocol.read_text(encoding='utf8'))
    for name in ('records','split'):
        if sha(policy[name]) != policy[name + '_sha256']:
            raise ValueError('Frozen input changed: ' + name)
    if (sha(args.dictionary) != 'e25161ab2f1626aaab8fbc0ae3f25d1a0603e32a7b759fc0406bb50783f70ba0'
            or sha(args.notebook) != '0bc83fdd9706f9f2d00274ec9207cb49d114af0a2e135774ffee3f4534770a21'):
        raise ValueError('Pinned publisher artifacts changed')
    notebook = json.loads(args.notebook.read_text(encoding='utf8'))
    source = '\n'.join(''.join(cell.get('source', [])) for cell in notebook['cells'])
    evidence = ['action_vocab.Name.to_list()', 'enumerate(action_names)', 'id_to_action[str(i[0])]', '[i/1000 for i in x]']
    if any(fragment not in source for fragment in evidence):
        raise ValueError('Publisher mapping or units convention missing')
    with args.dictionary.open(encoding='utf-8-sig', newline='') as stream:
        names = [r['Name'] for r in csv.DictReader(stream)]
    if len(names) != 1916 or len(set(names)) != 1916:
        raise ValueError('Unexpected dictionary')
    lookup = {i:i+2 for i in range(len(names))}
    ids, dt = prepare([{'actions': [[0], [], [1915]], 'times': [1000, 0, 500]}], lookup)
    if ids != [[2, 1, 1917]] or dt != [[1., 0., .5]]:
        raise ValueError('Frozen model index/unit mapping mismatch')
    with np.load(policy['split'], allow_pickle=False) as split:
        train_index = set(split['train'].tolist())
        dev_index = {row: i for i,row in enumerate(split['development'].tolist())}
    observed, train = set(), set()
    dev = [None] * len(dev_index)
    y = np.zeros(len(dev), dtype=bool)
    times, lengths = array('d'), {0:[], 1:[]}
    frequencies = {0:Counter(), 1:Counter()}
    shuffle_changed = Counter()
    index, invalid = 0, 0
    with Path(policy['records']).open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line)
            if not row['valid_sequence']:
                invalid += 1
                continue
            label = row['is_fraud']
            sequence = events(row)
            if any(type(x) is not int or x not in lookup for x in sequence):
                raise ValueError('Out of range event ID')
            observed.update(sequence)
            frequencies[label].update(sequence)
            times.extend(row['times'])
            lengths[label].append(len(row['times']))
            if len(row['times']) > 256:
                original = Counter(map(tuple, row['actions'][-256:]))
                shuffled = Counter(map(tuple, history_shuffle([row])[0]['actions'][-256:]))
                shuffle_changed[label] += (original != shuffled)
            if index in train_index:
                train.add(shingles(row))
            elif index in dev_index:
                pos = dev_index[index]
                dev[pos] = shingles(row)
                y[pos] = label
            index += 1
    if index != 105302 or invalid != 1 or len(lengths[1]) != 101 or len(observed) != 609 or (min(observed),max(observed)) != (1,1912):
        raise ValueError('Published event numbering or record counts changed')
    raw_times = np.asarray(times)
    if not np.isfinite(raw_times).all() or np.any(raw_times < 0):
        raise ValueError('Time values incompatible with nonnegative interval contract')
    truncation = {}
    for label in (0,1):
        lens = np.array(lengths[label])
        removed = np.maximum(lens - 256, 0)
        truncation[str(label)] = {'rows':len(lens), 'truncated_rows':int((removed > 0).sum()),
            'discarded_slots':int(removed.sum()), 'total_slots':int(lens.sum()), 'slot_lengths':summarize(lens),
            'registered_shuffle_changes_visible_event_multiset_rows':shuffle_changed[label]}
    mask = near_training(train, dev)
    args.output.mkdir(parents=True, exist_ok=False)
    np.save(args.output/'development-near-training.npy',mask)
    write(args.output/'audit.json', {'status':'development_input_contract_verified', 'dictionary_sha256':sha(args.dictionary),
        'publisher_notebook_sha256':sha(args.notebook), 'records_sha256':policy['records_sha256'], 'split_sha256':policy['split_sha256'],
        'observed_event_ids':len(observed), 'min_id':min(observed), 'max_id':max(observed), 'csv_row_zero':names[0],
        'index_contract':'Author enumerate starts at zero; event i uses CSV data row i and neural table row i+2.',
        'most_frequent_names_by_label':{str(label):{'author_zero_based':[names[x] for x,_ in frequency.most_common(10)],
            'hypothetical_one_based':[names[x-1] for x,_ in frequency.most_common(10)]} for label,frequency in frequencies.items()},
        'raw_times':summarize(raw_times), 'raw_zero_intervals':int((raw_times == 0).sum()),
        'unit_contract':'Author divides per slot by 1000 before mean/std; paper Fig 1 labels seconds. Notebook has a separate second division in total_time_to_transaction, which is not used.',
        'suffix_256_by_label':truncation, 'train_dev_near_rule':'Any train sequence with trigram-set Jaccard >= 0.9, full eligible candidates, no test membership queried.',
        'near_rows':int(mask.sum()), 'near_positives':int(y[mask].sum()), 'other_rows':int((~mask).sum()),
        'other_positives':int(y[~mask].sum()), 'near_mask_sha256':sha(args.output/'development-near-training.npy'),
        'test_predictions_generated':False})


if __name__ == '__main__':
    main()
