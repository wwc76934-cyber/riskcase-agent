"""Fetch two fixed public research files; local-only, no credentials or uploads."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

SOURCES={
    'creditcard':dict(directory='openml-creditcard-v1',filename='creditcard.arff',
        url='https://openml.org/data/v1/download/1673544/creditcard.arff',bytes=150829308,
        sha256='fdaf12730dc1fc426f318b71349f24f5c5fd00aa1152940be7e7509ae3d89d2a'),
    'longmemeval_s':dict(directory='longmemeval-s-cleaned',filename='longmemeval_s_cleaned.json',
        url='https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_s_cleaned.json',
        bytes=277383467,sha256='d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442')}


def verified(path,spec):
    if path.stat().st_size!=spec['bytes']:raise ValueError('Declared file size mismatch')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=spec['sha256']:raise ValueError('Fixed source checksum mismatch')


def fetch(name,root):
    spec=SOURCES[name];directory=root/spec['directory'];directory.mkdir(parents=True,exist_ok=True)
    path=directory/spec['filename']
    if path.exists():verified(path,spec);return dict(path=str(path),state='existing_verified',sha256=spec['sha256'])
    partial=path.with_suffix(path.suffix+'.partial');size=0
    with urllib.request.urlopen(spec['url'],timeout=45) as response,partial.open('wb') as handle:
        while block:=response.read(1024*1024):
            size+=len(block)
            if size>spec['bytes']:raise ValueError('Declared download size exceeded')
            handle.write(block)
    verified(partial,spec);partial.rename(path)
    return dict(path=str(path),state='downloaded_verified',sha256=spec['sha256'],bytes=size)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',choices=SOURCES)
    parser.add_argument('--download',action='store_true',help='Explicitly permit the public HTTPS download')
    args=parser.parse_args()
    if not args.download:parser.error('--download required; this fetches the fixed public file into ignored data/raw')
    print(json.dumps(fetch(args.source,Path('data/raw'))))


if __name__=='__main__':main()
