"""Download only the approved public encoder revision, verify official weight hash.

Uses the user's normal HTTPS certificate verification; no paid endpoint or data upload.
An incomplete file is never accepted as a model. Existing complete files are reused.
"""
import argparse
import hashlib
import json
from pathlib import Path


REPO = 'Qwen/Qwen3-Embedding-0.6B'
REVISION = '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3'
WEIGHT_SHA = '0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd'
WEIGHT_BYTES = 1191586416
FILES = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt', 'model.safetensors']


def main():
    from huggingface_hub import hf_hub_download
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        hf_hub_download(REPO, name, revision=REVISION, local_dir=args.output)
    path = args.output / 'model.safetensors'
    hasher = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            hasher.update(block)
    if path.stat().st_size != WEIGHT_BYTES or hasher.hexdigest() != WEIGHT_SHA:
        raise ValueError('Incomplete/corrupt weight; keep failure and retry download, never train with it')
    (args.output / 'revision.json').write_text(json.dumps({'repo': REPO, 'revision': REVISION,
        'license': 'apache-2.0', 'source': 'https://huggingface.co/' + REPO,
        'official_weight_sha256': WEIGHT_SHA, 'official_weight_bytes': WEIGHT_BYTES}, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'official_encoder_verified', 'revision': REVISION, 'sha256': WEIGHT_SHA}))


if __name__ == '__main__':
    main()
