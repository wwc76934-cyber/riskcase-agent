"""Audited public category text, fixed local encoder and content-addressed cache."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np
import torch

from .common import digest, fingerprint, write_json

CHANNELS = {'Chip Transaction': 'chip card transaction', 'Swipe Transaction': 'swipe card transaction',
            'Online Transaction': 'online transaction'}
UNKNOWN = 'unknown'
TEMPLATE = 'Merchant category: {mcc}. Transaction channel: {channel}.'
ENCODER_FILES = ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt', 'model.safetensors')


def encoder_fingerprint(directory):
    # Downloader timestamps, .cache records and local revision formatting must
    # not change the identity of otherwise identical model/tokenizer bytes.
    return {name: digest(Path(directory) / name) for name in ENCODER_FILES}


def cache_identity(manifest):
    value = {'texts': manifest['texts'], 'encoder_files': manifest['encoder_files'],
             'dimensions': manifest['dimensions'], 'glossary_sha256': manifest['glossary_sha256'],
             'pooling': manifest['pooling'], 'max_length': manifest['max_length']}
    if manifest['version'] >= 2:
        value.update(encoder_revision=manifest['encoder']['revision'], keys=manifest['keys'])
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def category_key(mcc, channel):
    # No coercion of 5411.7, NaN, injected descriptions or merchant identifiers.
    mcc = str(mcc).strip() if mcc is not None else ''
    channel = str(channel).strip() if channel is not None else ''
    return (mcc if re.fullmatch(r'[0-9]{4}', mcc) else UNKNOWN,
            channel if channel in CHANNELS else UNKNOWN)


def category_text(key, glossary):
    mcc, channel = key
    description = glossary['entries'].get(mcc, {}).get('title', UNKNOWN)
    return TEMPLATE.format(mcc=description, channel=CHANNELS.get(channel, UNKNOWN))


def last_token_pool(hidden, mask):
    # Correct with left/right padding; reject empty sequences.
    if not torch.all(mask.sum(1) > 0):
        raise ValueError('Empty token sequence')
    positions = torch.arange(mask.shape[1], device=mask.device).expand_as(mask)
    last = positions.masked_fill(mask == 0, -1).max(1).values
    return hidden[torch.arange(len(hidden), device=hidden.device), last]


def build_cache(keys, glossary_path, model_path, output, *, dimensions=32, batch_size=8):
    """Only receives training category identities, not transaction rows or outcomes."""
    from transformers import AutoModel, AutoTokenizer
    output, model_path = Path(output), Path(model_path)
    output.mkdir(parents=True, exist_ok=False)
    glossary = json.loads(Path(glossary_path).read_text(encoding='utf-8'))
    revision = json.loads((model_path / 'revision.json').read_text(encoding='utf-8'))
    if revision['repo'] != 'Qwen/Qwen3-Embedding-0.6B' or not re.fullmatch(r'[a-f0-9]{40}', revision['revision']):
        raise ValueError('Pinned approved encoder required')
    if revision['revision'] != '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3' or digest(model_path / 'model.safetensors') != '0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd':
        raise ValueError('Official fixed-revision weight SHA256 differs')
    keys = [(UNKNOWN, UNKNOWN)] + sorted(set(keys) - {(UNKNOWN, UNKNOWN)})
    texts = [category_text(key, glossary) for key in keys]
    unique = sorted(set(texts))
    if not 32 <= dimensions <= 1024:
        raise ValueError('Supported Matryoshka dimension required')
    torch.set_num_threads(4)
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, padding_side='left')
    model = AutoModel.from_pretrained(model_path, local_files_only=True, trust_remote_code=False,
                                     dtype=torch.float32)
    model.eval()
    encoded = {}
    with torch.inference_mode():
        for start in range(0, len(unique), batch_size):
            batch = unique[start:start + batch_size]
            inputs = tokenizer(batch, padding=True, truncation=True, max_length=128, return_tensors='pt')
            hidden = model(**inputs).last_hidden_state
            full = torch.nn.functional.normalize(last_token_pool(hidden, inputs['attention_mask']), dim=1)
            vectors = torch.nn.functional.normalize(full[:, :dimensions], dim=1).cpu().numpy()
            encoded.update(zip(batch, vectors))
            print(f'encoded {min(start + batch_size, len(unique))}/{len(unique)} unique texts', flush=True)
    table = np.stack([encoded[text] for text in texts]).astype(np.float32)
    if not np.isfinite(table).all():
        raise ValueError('Nonfinite semantic vectors')
    np.save(output / 'vectors.npy', table)
    manifest = {'version': 2, 'encoder': revision, 'pooling': 'last_nonpadding_token',
                'normalization': 'L2 full, first dimensions, L2 again', 'dimensions': dimensions,
                'max_length': 128, 'dtype': 'float32', 'keys': keys, 'texts': texts,
                'template': TEMPLATE, 'glossary_sha256': digest(glossary_path),
                'encoder_files': encoder_fingerprint(model_path), 'vectors_sha256': digest(output / 'vectors.npy'),
                'created_at': datetime.now(timezone.utc).isoformat(),
                'elapsed_seconds': time.monotonic() - started, 'unique_texts': len(unique),
                'source_scope': 'training categories plus explicit unknown; no evaluation categories fitted',
                'transformers': __import__('transformers').__version__, 'torch': torch.__version__}
    manifest['cache_key'] = cache_identity(manifest)
    write_json(output / 'manifest.json', manifest)
    return manifest


def load_cache(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if digest(directory / 'vectors.npy') != manifest['vectors_sha256']:
        raise ValueError('Embedding cache changed')
    table = np.load(directory / 'vectors.npy', allow_pickle=False)
    keys = [tuple(key) for key in manifest['keys']]
    if table.shape != (len(keys), manifest['dimensions']) or len(set(keys)) != len(keys) or not np.isfinite(table).all():
        raise ValueError('Invalid embedding cache')
    if keys[0] != (UNKNOWN, UNKNOWN):
        raise ValueError('Unknown row required')
    if (manifest.get('version') not in (1, 2) or keys[1:] != sorted(keys[1:])
            or len(manifest['texts']) != len(keys) or any(category_key(*key) != key for key in keys)
            or cache_identity(manifest) != manifest['cache_key']):
        raise ValueError('Cache identity or canonical category binding changed')
    return manifest, table


def attest_cache_v2(source, encoder, output):
    """Reuse already verified vector bytes; never relabel this as a fresh encoding."""
    from copy import deepcopy
    import shutil
    old, _ = load_cache(source)
    core = encoder_fingerprint(encoder)
    if any(old['encoder_files'].get(name) != value for name, value in core.items()):
        raise ValueError('Encoder/tokenizer changed; fresh encoding required')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(source) / 'vectors.npy', output / 'vectors.npy')
    new = deepcopy(old)
    new.update(version=2, encoder_files=core, reused_vectors_from_manifest_sha256=digest(Path(source) / 'manifest.json'),
               attested_at=datetime.now(timezone.utc).isoformat(),
               attestation='Same keys/texts/dimensions and encoder bytes; original encoding time retained, zero new forward calls')
    new['cache_key'] = cache_identity(new)
    write_json(output / 'manifest.json', new)
    load_cache(output)
    return new
