"""Offline local encoder; input is1916 public API names, not bank records."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import numpy as np
import torch
from app.risk.behavior_semantic.semantics import ontology,normalize,static_tables
from app.risk.behavior_semantic.study import write,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--dictionary',required=True);p.add_argument('--encoder',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    from transformers import AutoTokenizer,AutoModel
    encoder=Path(a.encoder);revision=json.loads((encoder/'revision.json').read_text())
    if revision['repo']!='Qwen/Qwen3-Embedding-0.6B' or revision['revision']!='97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3' or revision['license']!='apache-2.0':raise ValueError('Reviewed encoder revision required')
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);texts=ontology(a.dictionary);torch.set_num_threads(2)
    tokenizer=AutoTokenizer.from_pretrained(encoder,local_files_only=True,trust_remote_code=False,padding_side='left')
    model=AutoModel.from_pretrained(encoder,local_files_only=True,trust_remote_code=False,torch_dtype=torch.float32).eval()
    vectors=[];tick=time.monotonic();cpu=time.process_time()
    with torch.inference_mode():
        for start in range(0,len(texts),8):
            batch=tokenizer(texts[start:start+8],padding=True,truncation=True,max_length=192,return_tensors='pt')
            hidden=model(**batch).last_hidden_state[:,-1,:];vectors.append(hidden.numpy())
            if start%128==0:print('encoded_public_events',start,flush=True)
    full=np.vstack(vectors);np.save(out/'qwen-full.npy',full)
    table=normalize(np.vstack([np.zeros((2,32)),full[:,:32]]));tables=static_tables(texts,table)
    for name,value in tables.items():np.save(out/(name+'.npy'),value)
    write(out/'manifest.json',{'status':'public_ontology_encoded_offline','revision':revision,'dictionary_sha256':sha(a.dictionary),
        'texts':texts,'text_sha256':hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest(),
        'encoder_files_sha256':{f.name:sha(f) for f in encoder.iterdir() if f.is_file()},
        'table_sha256':{k:sha(out/(k+'.npy')) for k in tables},'vectors_shape':[1918,32],'wall_seconds':time.monotonic()-tick,'CPU_seconds':time.process_time()-cpu,
        'input_bank_records':0,'input_labels':0,'new_downloads':0,'paid_calls':0,'encoder_parameters':sum(p.numel() for p in model.parameters())})


if __name__=='__main__':main()
