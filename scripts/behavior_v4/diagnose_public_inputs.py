"""Inspect API-name coverage and centering geometry without fraud data."""
import argparse
import csv
import json
from pathlib import Path
import re

import numpy as np

from app.risk.behavior_semantic.semantics import VERBS
from scripts.behavior_v3.audit_public_geometry import describe, sha


def unit(x):
    return x/np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-12)


def main():
    parser=argparse.ArgumentParser()
    for name in ('dictionary','bank','protocol','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    protocol=json.loads(args.protocol.read_text(encoding='utf8'))
    manifest_path=args.bank/'manifest.json'
    if sha(manifest_path)!=protocol['bank_manifest_sha256']:
        raise ValueError('Public encoder receipt changed')
    manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    if sha(args.dictionary)!=manifest['dictionary_sha256'] or manifest['input_bank_records'] or manifest['input_labels']:
        raise ValueError('Public dictionary only')
    full_path=args.bank/'qwen-full.npy'
    if sha(full_path)!=protocol['qwen_full_sha256']:
        raise ValueError('Public vector table changed')
    with args.dictionary.open(encoding='utf-8-sig',newline='') as stream:
        names=[row['Name'] for row in csv.DictReader(stream)]
    operations=[name.strip('/').split('/')[-1] for name in names]
    ops_lexical={
        'operation_has_configured_verb_prefix':sum(op.upper().startswith(tuple(VERBS)) for op in operations),
        'operation_is_single_uppercase_alphanumeric_token':sum(bool(re.fullmatch('[A-Z0-9]+',op)) for op in operations),
        'operation_short_at_most_four_characters':sum(len(op)<=4 for op in operations),
        'distinct_terminal_names':len(set(operations)),
    }
    full=np.load(full_path,allow_pickle=False)
    q32=unit(full[:,:32])
    center=q32.mean(axis=0)
    q32c=unit(q32-center)
    rng=np.random.default_rng(20261109)
    pairs=rng.integers(0,len(names),size=(5000,2))
    pairs=pairs[pairs[:,0]!=pairs[:,1]]
    report={'events':len(names),'input_bank_records':0,'input_labels':0,'lexical_proxies':ops_lexical,
        'mean_direction_squared_norm':float(np.dot(center,center)),
        'qwen32':describe(q32,names,pairs),'qwen32_centered':describe(q32c,names,pairs),
        'qwen128':describe(full[:,:128],names,pairs),
        'scope':'Public-path proxies and shared direction. Template causality and banking intent are not identified.'}
    with args.output.open('x',encoding='utf8') as stream:
        json.dump(report,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    main()
