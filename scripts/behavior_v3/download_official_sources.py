import urllib.request,hashlib,json
from pathlib import Path
root=Path('behavior-semantic-fraud/runs/behavior-v3-source-01')
commit='76bf718418e80549c49ac615857f8edda352ec99'
files=['README.md','vocab.csv','Fraud Detection with NLP.ipynb','Fraud Detection with Natural Language Processing.rar']
rows=[]
for name in files:
 url='https://raw.githubusercontent.com/pboulieris/FraudNLP/'+commit+'/'+urllib.parse.quote(name)
 p=root/name
 if p.exists(): raise FileExistsError(p)
 with urllib.request.urlopen(url,timeout=90) as r: data=r.read(30_000_001)
 assert len(data)<=30_000_000
 p.write_bytes(data);rows.append(dict(file=name,url=url,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),git_blob_sha1=hashlib.sha1(('blob '+str(len(data))+'\0').encode()+data).hexdigest()))
(root/'manifest.json').write_text(json.dumps({'commit':commit,'files':rows,'executed_external_code':False,'unpickled':False},indent=2),encoding='utf8')
print(json.dumps(rows,indent=2))
