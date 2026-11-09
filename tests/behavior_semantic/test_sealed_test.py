"""Regression: development driver must not index the poison final-test partition."""
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
from app.risk.behavior_semantic.study import sha


def test_development_driver_never_reads_test_partition(tmp_path,monkeypatch):
    source=Path(__file__).resolve().parents[2]/'scripts/behavior_v3/run_baselines.py'
    spec=importlib.util.spec_from_file_location('track_a_fixture_driver',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rows=[{'actions':[[1],[2]],'times':[0,10],'Amount':10.+i,'device_freq':.1,'ip_freq':.2,'beneficiary_freq':.3,'application_freq':.4,'is_fraud':int(i%4==0)} for i in range(52)]
    data=tmp_path/'self_authored.txt';data.write_text('not bank data')
    split=tmp_path/'split.npz';np.savez(split,train=np.arange(40),development=np.arange(40,52),test=np.array([999999999]))
    protocol={'status':'frozen_before_A2_training','conversion_independently_verified':True,'permission_source':'https://github.com/pboulieris/FraudNLP/issues/4#issuecomment-2514497586',
        'code_sha256':{},'records':str(data),'records_sha256':sha(data),'split':str(split),'split_sha256':sha(split),'models':['numeric_lr'],'seeds':[42],'C':[1.],'release_final_test':False}
    pp=tmp_path/'protocol.json';pp.write_text(json.dumps(protocol));out=tmp_path/'result'
    monkeypatch.setattr(module,'read_records',lambda p:(rows,[]));monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**k:b'')
    monkeypatch.setattr(sys,'argv',['fixture','--protocol',str(pp),'--output',str(out)])
    module.main();result=json.loads((out/'development-results.json').read_text())
    assert not result['final_test_predictions_generated']
    assert not list(out.rglob('*test-scores.npy'))
