from pathlib import Path
import urllib.request,hashlib,subprocess,json
r=Path('behavior-semantic-fraud/runs/behavior-v3-tools-01');r.mkdir(exist_ok=False)
for name,h in [('7zr.exe','9bf582b894dfa665f852d7e9112b02290a1c303c2d95a21fcdc41ffcb03143b7'),('7z2605-x64.exe','fd99e86f57d8f429ef5ed045bf98315af962e2309b0f1ba87a147a44c71647d0')]:
 b=urllib.request.urlopen('https://github.com/ip7z/7zip/releases/download/26.05/'+name,timeout=90).read();assert hashlib.sha256(b).hexdigest()==h;(r/name).write_bytes(b)
subprocess.run([str(r/'7zr.exe'),'x',str(r/'7z2605-x64.exe'),'-o'+str(r/'portable'),'-y'],check=True,stdout=subprocess.PIPE)
print('Official release digests verified; installer unpacked, not installed')
