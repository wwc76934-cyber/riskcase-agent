"""CPU inference using pinned local weights, no remote code or runtime downloads."""
import hashlib
import json
from pathlib import Path


class LocalModels:
    def __init__(self,manifest):
        import torch
        from transformers import AutoTokenizer,AutoModel,AutoModelForSequenceClassification
        self.torch=torch;torch.set_num_threads(2)
        self.identity=json.loads(Path(manifest).read_text(encoding='utf-8'))
        for info in self.identity.values():
            for name,expected in info['sha256'].items():
                root=Path(info['path']).resolve();path=(root/name).resolve()
                if not path.is_relative_to(root):raise ValueError('Model manifest path escapes root')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Model artifact changed')
        enc=self.identity['encoder']['path'];rank=self.identity['reranker']['path']
        self.tokenizer=AutoTokenizer.from_pretrained(enc,local_files_only=True,trust_remote_code=False)
        self.encoder=AutoModel.from_pretrained(enc,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval()
        self.rank_tokenizer=AutoTokenizer.from_pretrained(rank,local_files_only=True,trust_remote_code=False)
        self.ranker=AutoModelForSequenceClassification.from_pretrained(rank,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval()
        self.cache={}

    def encode(self,texts):
        inputs=self.tokenizer(texts,padding=True,truncation=True,max_length=256,return_tensors='pt')
        with self.torch.inference_mode():vectors=self.encoder(**inputs).last_hidden_state[:,0]
        return self.torch.nn.functional.normalize(vectors,p=2,dim=1)

    def similarities(self,query,documents):
        key=tuple(documents)
        if key not in self.cache:
            if len(self.cache)>=4:self.cache.clear()
            self.cache[key]=self.encode(documents)
        query_vector=self.encode(['为这个句子生成表示以用于检索相关文章：'+query])
        return (query_vector@self.cache[key].T)[0].tolist()

    def rerank(self,query,documents):
        inputs=self.rank_tokenizer([query]*len(documents),documents,padding=True,truncation=True,max_length=256,return_tensors='pt')
        with self.torch.inference_mode():return self.ranker(**inputs).logits.reshape(-1).tolist()
