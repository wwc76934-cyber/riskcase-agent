"""One-shot A2 -> public ontology encoding -> registered A3; no final-test release."""
import argparse
import json
import os
from pathlib import Path
import site
import subprocess
import sys
import time
import traceback

parser=argparse.ArgumentParser()
parser.add_argument('--protocol',default='config/behavior-semantic/track-a-a3.json')
parser.add_argument('--output',default='runs/track-a-continuation-01')
parser.add_argument('--a2-transformer-retry')
cli=parser.parse_args()
root=Path.cwd();site.addsitedir(str(root.parent/'financial-report-agent/.venv-service-addons'));sys.path.insert(0,str(root))
env=os.environ.copy();env['PYTHONPATH']=os.pathsep.join(str(p) for p in sys.path if p)
env['PYTHONDONTWRITEBYTECODE']='1';env['OMP_NUM_THREADS']='2';env['MKL_NUM_THREADS']='2';env['OPENBLAS_NUM_THREADS']='2'
folder=root/cli.output;folder.mkdir(parents=True,exist_ok=False)
read=lambda p:json.loads(Path(p).read_text(encoding='utf8'))
write=lambda name,obj:(folder/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
python=getattr(sys,'_base_executable',sys.executable)
steps=[('encode',['scripts/behavior_v3/encode_public_events.py','--dictionary','runs/behavior-v3-source-01/vocab.csv','--encoder',str(root.parent/'semantic-detector-v2/runs/semantic-detection-models/qwen3-0.6b'),'--output','runs/track-a-public-event-bank-01']),
       ('A3',['scripts/behavior_v3/run_semantic.py','--protocol',cli.protocol,'--output','runs/track-a-a3-01']),
       ('development-analysis',['scripts/behavior_v3/analyze_development.py','--A2',read(cli.protocol)['A2_run'],
           '--A3','runs/track-a-a3-01','--output','runs/track-a-development-analysis-01'])]
write('protocol.json',{'steps':steps,'wait_for':'A2 development-only results and completed all-training marker','release_final_test':False,'paid_calls':0})
try:
    parent=root/read(cli.protocol)['A2_run']
    if cli.a2_transformer_retry:
        retry=Path(cli.a2_transformer_retry)
        deadline=time.monotonic()+8*3600
        while not (retry/'development-results.json').is_file():
            if (folder/'stop.request').exists():
                write('cancelled.json',{'status':'stopped during A2 recovery'});raise SystemExit(0)
            if time.monotonic()>deadline:raise TimeoutError('Transformer retry did not finish within the coordinator wait')
            time.sleep(30)
        with (folder/'merge-A2.log').open('x',encoding='utf8') as stream:
            subprocess.run([python,'-X','utf8','-B','-m','scripts.behavior_v3.merge_a2_recovery',
                '--original','runs/track-a-a2-01','--retry',str(retry),'--output',str(parent)],
                cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
    while not (parent/'development-results.json').is_file():
        if (folder/'stop.request').exists():write('cancelled.json',{'status':'stopped before next stage'});raise SystemExit(0)
        observed=root/'runs/track-a-a2-resources-01/report.json'
        if observed.exists():raise RuntimeError('A2 process exited without expected development-only results; preserve failure and stop')
        time.sleep(30)
    prior=read(parent/'development-results.json')
    if prior['status']!='A2_development_comparison_complete_test_still_sealed' or prior['final_test_predictions_generated']:
        raise ValueError('A2 stage gate mismatch')
    if read(parent/'all-training-complete.json')['instances']!=21:raise ValueError('Incomplete A2 model matrix')
    for name,args in steps:
        if (folder/'stop.request').exists():write('cancelled.json',{'status':'stopped before '+name});raise SystemExit(0)
        print(name,'start',time.strftime('%Y-%m-%d %H:%M:%S'),flush=True);tick=time.monotonic()
        with (folder/(name+'.log')).open('x',encoding='utf8') as stream:
            process=subprocess.Popen([python,'-X','utf8','-B',*args],cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT)
            write(name+'-process.json',{'pid':process.pid,'actual_executable':python,'command':args})
            with (folder/(name+'-observer.log')).open('x',encoding='utf8') as observer_log:
                observer=subprocess.Popen([python,'-X','utf8','-B','-m','app.risk.behavior_semantic.process_observer','--pid',str(process.pid),'--output',str(folder/(name+'-resources'))],cwd=root,env=env,stdout=observer_log,stderr=subprocess.STDOUT)
                code=process.wait();observer_code=observer.wait()
        write(name+'-exit.json',{'exit_code':code,'observer_exit_code':observer_code,'wall_seconds':time.monotonic()-tick})
        if code:raise RuntimeError(name+' failed; no automatic retry, exit='+str(code))
        print(name,'complete',time.strftime('%Y-%m-%d %H:%M:%S'),flush=True)
    write('complete.json',{'status':'A2_and_registered_A3_development_stages_complete','final_test_still_sealed':True,'manual_A4_gate_and_final_evaluation_required':True})
except Exception as exc:
    write('failure.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()});raise
