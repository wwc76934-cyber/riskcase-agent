"""Download public safetensors once; normal retrieval never downloads models."""
import json
import hashlib
from pathlib import Path
from huggingface_hub import snapshot_download


def main():
    root=Path('runs/rag-models');root.mkdir(parents=True,exist_ok=True)
    records={}
    config=json.loads((Path(__file__).resolve().parents[2]/'config/rag-models-v1.json').read_text(encoding='utf-8'))
    for role,info in config.items():
        repo=info['repo'];revision=info['revision']
        destination=root/role
        snapshot_download(repo,revision=revision,token=False,local_dir=destination,
                          allow_patterns=['*.json','*.txt','*.model','model.safetensors'],max_workers=2)
        if not (destination/'model.safetensors').exists():raise ValueError('Safetensors weights required')
        records[role]=dict(repo=repo,revision=revision,path=str(destination.resolve()),
            sha256={p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file() and '.cache' not in p.parts})
        print(json.dumps(dict(role=role,revision=revision),ensure_ascii=False),flush=True)
    (root/'manifest.json').write_text(json.dumps(records,indent=2),encoding='utf-8')


if __name__=='__main__':main()
