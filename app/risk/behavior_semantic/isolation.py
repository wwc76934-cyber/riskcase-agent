"""Build an explicit offline OS-container boundary; never fall back to host conversion."""
from pathlib import Path
import re


def conversion_command(image,input_file,script,output,name):
    if not re.fullmatch(r'python@sha256:[0-9a-f]{64}',image):
        raise ValueError('Pinned official Python image digest required')
    if not re.fullmatch(r'fraudnlp-convert-[a-z0-9-]+',name):raise ValueError('Dedicated container name required')
    source,code,dest=[Path(p).resolve() for p in (input_file,script,output)]
    if not source.is_file() or not code.is_file() or not dest.is_dir() or any(dest.iterdir()):
        raise ValueError('Readonly inputs and a fresh empty output directory required')
    for p in (source,code,dest):
        if ',' in str(p):raise ValueError('Comma in bind mount rejected')
    return ['docker','run','--name',name,'--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--pids-limit','64','--memory','2g','--cpus','2',
        '--user','65534:65534','--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
        '--mount',f'type=bind,src={source},dst=/input/data.pkl,readonly',
        '--mount',f'type=bind,src={code},dst=/tool/convert.py,readonly',
        '--mount',f'type=bind,src={dest},dst=/output',image,
        'python','-I','-S','-B','/tool/convert.py','--input','/input/data.pkl','--output','/output/converted']
