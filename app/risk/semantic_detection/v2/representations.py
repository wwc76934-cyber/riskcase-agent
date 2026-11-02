"""Label-free category-only frozen encodings and bounded geometry diagnostics."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.metrics import balanced_accuracy_score

from app.risk.semantic_detection.common import digest, fingerprint, write_json
from app.risk.semantic_detection.semantics import (CHANNELS, UNKNOWN, category_key, category_text,
                                                 encoder_fingerprint, last_token_pool, load_cache)


def industry_title(code, title):
    """Limited, declared abstraction of brand MCCs; all other titles unchanged."""
    if title == UNKNOWN:
        return title
    number = int(code) if code.isdigit() else -1
    if 3000 <= number <= 3350:
        return 'Airlines, passenger air transportation'
    if 3351 <= number <= 3441:
        return 'Car rental agencies'
    if 3501 <= number <= 3999:
        return 'Lodging, hotels and motels'
    return title


def texts_for(keys, glossary, *, channel, mode='original'):
    if mode not in {'original', 'industry', 'description_shuffle'}:
        raise ValueError('Frozen text template mode required')
    codes = sorted({m for m, _ in keys if m in glossary['entries']})
    shuffled = np.random.default_rng(731).permutation(codes).tolist()
    mapping = dict(zip(codes, shuffled))
    texts = []
    for mcc, c in keys:
        code = mapping.get(mcc, mcc) if mode == 'description_shuffle' else mcc
        title = glossary['entries'].get(code, {}).get('title', UNKNOWN)
        if mode == 'industry':
            title = industry_title(mcc, title)
        text = f'Merchant category: {title}.'
        if channel:
            text += f' Transaction channel: {CHANNELS.get(c, UNKNOWN)}.'
        texts.append(text)
    return texts


def prepare(X, glossary_path, model_path, original_cache, output):
    """Receives only preallowed category fields, never labels or row identities."""
    from transformers import AutoTokenizer, AutoModel
    if list(X.columns) != ['mcc', 'channel']:
        raise ValueError('Encoder accepts category fields only')
    output, model_path = Path(output), Path(model_path)
    output.mkdir(parents=True, exist_ok=False)
    encoder = encoder_fingerprint(model_path)
    if encoder['model.safetensors'] != '0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd':
        raise ValueError('Pinned public encoder weight mismatch')
    revision = json.loads((model_path / 'revision.json').read_text(encoding='utf8'))
    if revision['revision'] != '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3':
        raise ValueError('Pinned revision required')
    glossary = json.loads(Path(glossary_path).read_text(encoding='utf8'))
    specifications = []
    for channel in (False, True):
        observed = {category_key(m, c) if channel else (category_key(m, c)[0], UNKNOWN) for m, c in zip(X.mcc, X.channel)}
        keys = [(UNKNOWN, UNKNOWN)] + sorted(observed - {(UNKNOWN, UNKNOWN)})
        for mode in ('original', 'industry', 'description_shuffle'):
            texts = texts_for(keys, glossary, channel=channel, mode=mode)
            specifications.append({'name': f'{"channel" if channel else "nochannel"}-{mode}', 'channel': channel,
                                   'mode': mode, 'keys': keys, 'texts': texts})
    old, old_table = load_cache(original_cache)
    # Original 272 unique strings first, preserving their original batch grouping.
    unique = sorted(set(old['texts']))
    unique += sorted({text for s in specifications for text in s['texts']} - set(unique))
    torch.set_num_threads(4); started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, padding_side='left')
    model = AutoModel.from_pretrained(model_path, local_files_only=True, trust_remote_code=False, dtype=torch.float32)
    model.eval(); encoded = {}
    with torch.inference_mode():
        for offset in range(0, len(unique), 8):
            batch = unique[offset:offset + 8]
            inputs = tokenizer(batch, padding=True, truncation=True, max_length=128, return_tensors='pt')
            hidden = model(**inputs).last_hidden_state
            full = torch.nn.functional.normalize(last_token_pool(hidden, inputs['attention_mask']), dim=1).numpy()
            encoded.update(zip(batch, full))
            print(f'v21-frozen-encoder {min(offset+8,len(unique))}/{len(unique)}', flush=True)
    original = np.stack([encoded[text] for text in old['texts']])[:, :32]
    original /= np.linalg.norm(original, axis=1, keepdims=True)
    difference = float(np.max(np.abs(original - old_table)))
    if difference > 1e-5:
        raise ValueError(f'Original 32-dimensional cache replay differs: {difference}')
    for specification in specifications:
        vectors = np.stack([encoded[text] for text in specification['texts']]).astype(np.float32)
        np.save(output / (specification['name'] + '.npy'), vectors)
        specification['vectors_sha256'] = digest(output / (specification['name'] + '.npy'))
    manifest = {'specifications': specifications, 'encoder': revision, 'encoder_files': encoder,
                'glossary_sha256': digest(glossary_path), 'dimensions_stored': 1024, 'slice_dimensions': [32,128,256],
                'unique_texts': len(unique), 'original_unique_texts': len(set(old['texts'])),
                'original_cache_sha256': digest(Path(original_cache) / 'manifest.json'),
                'original32_max_abs_diff': difference, 'elapsed_seconds': time.monotonic() - started,
                'unknown_auxiliary_policy': 'Zero at downstream model input in all representations',
                'source_scope': 'Training category identities only; modern reference, retrospective research',
                'industry_rules': 'Visa pp30-37: 3000-3350 airlines,3351-3441 car rental;3501-3999 lodging; applied only to known glossary titles',
                'shuffle_seed': 731, 'paid_calls': 0}
    write_json(output / 'manifest.json', manifest)
    return manifest


def table_for(bank, *, channel, mode='original', dimensions=32):
    if dimensions not in (32,128,256):
        raise ValueError('Approved dimensions required')
    bank = Path(bank)
    manifest = json.loads((bank / 'manifest.json').read_text(encoding='utf8'))
    spec, = [s for s in manifest['specifications'] if s['channel'] == channel and s['mode'] == mode]
    path = bank / (spec['name'] + '.npy')
    if digest(path) != spec['vectors_sha256']:
        raise ValueError('Frozen representation bank changed')
    table = np.load(path, allow_pickle=False)[:, :dimensions].copy()
    if table.shape != (len(spec['keys']), dimensions) or not np.isfinite(table).all():
        raise ValueError('Invalid finite category representation')
    table /= np.maximum(np.linalg.norm(table, axis=1, keepdims=True), 1e-12)
    return spec['keys'], table


def geometry(original_cache, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    manifest, table = load_cache(original_cache)
    # One representative for each of the 272 unique original texts.
    rows = [manifest['texts'].index(t) for t in sorted(set(manifest['texts']))]
    vectors = table[rows]
    keys = [manifest['keys'][i] for i in rows]
    texts = [manifest['texts'][i] for i in rows]
    cosine = vectors @ vectors.T
    np.save(output / 'original272-cosine.npy', cosine)
    np.fill_diagonal(cosine, -np.inf)
    neighbors = np.argsort(-cosine, axis=1)[:, :5]
    examples = [{'key': keys[i], 'text': texts[i],
                 'neighbors': [{'key': keys[j], 'cosine': float(cosine[i,j]), 'text': texts[j]} for j in neighbors[i]]}
                for i in range(len(rows))]
    groups = np.array([key[0] for key in keys]); labels = np.array([key[1] for key in keys])
    valid = labels != UNKNOWN
    folds = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=731)
    accuracy, checks = [], []
    for train, test in folds.split(vectors[valid], labels[valid], groups[valid]):
        selected = np.flatnonzero(valid)
        a, b = selected[train], selected[test]
        overlap = set(groups[a]) & set(groups[b])
        if overlap:
            raise ValueError('MCC overlap in channel geometry diagnostic')
        classifier = LogisticRegression(C=1., max_iter=1000).fit(vectors[a], labels[a])
        accuracy.append(float(balanced_accuracy_score(labels[b], classifier.predict(vectors[b]))))
        checks.append({'train_rows':len(a),'validation_rows':len(b),'mcc_overlap':len(overlap)})
    report = {'unique_texts': len(rows), 'dimensions': table.shape[1], 'nearest5': examples,
              'nearest1_same_channel_fraction': float(np.mean([keys[i][1] == keys[j[0]][1] for i,j in enumerate(neighbors)])),
              'channel_balanced_accuracy_grouped_by_mcc': accuracy, 'fold_checks': checks,
              'chance_balanced_accuracy': 1/3, 'classification_scope': 'Category geometry, not fraud effectiveness',
              'cache_sha256': digest(Path(original_cache) / 'manifest.json')}
    write_json(output / 'report.json', report)
    return report
