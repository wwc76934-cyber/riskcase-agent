"""One fixed, explicitly posthoc stronger deterministic-history tree control."""
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from threadpoolctl import threadpool_limits

from app.risk.ml_models import MODEL_COLUMNS
from app.risk.semantic_detection.common import digest,write_json,peak_working_set_bytes
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .history import STAT_NAMES


def run(source_root,history_run,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);history_run=Path(history_run)
    params={'max_iter':100,'max_leaf_nodes':15,'learning_rate':.1,'min_samples_leaf':50,
            'l2_regularization':1.,'early_stopping':False,'class_weight':'balanced','random_state':42}
    protocol={'type':'Posthoc fixed stronger control registered after Mean ensemble215 and self137 were observed',
              'params':params,'threads':2,'original_primary_protocol_unmodified':True,'paid_calls':0,
              'code_sha256':digest(__file__),'history_protocol_sha256':digest(history_run/'protocol.json')}
    write_json(output/'protocol.json',protocol)
    try:
        (X,y,meta),(VX,vy,vmeta)=(loaded:=load_data(source_root))[0]
        expected=json.loads((history_run/'graph.json').read_text(encoding='utf8'))
        ids=hashlib.sha256('\n'.join(meta.transaction_id).encode()).hexdigest()
        if ids!=expected['training']['node_ids_sha256']:raise ValueError('History statistic row alignment differs')
        train=np.load(history_run/'training-statistics.npy',allow_pickle=False)
        context=np.load(history_run/'inference-statistics.npy',allow_pickle=False)
        if train.shape!=(len(X),14) or context.shape!=(len(X)+len(VX),14) or not np.array_equal(train,context[:len(X)]):
            raise ValueError('Shared strict-past training statistic values differ')
        numeric=[c for c in MODEL_COLUMNS if c not in ('mcc','channel')]
        def assemble(frame,statistics):
            result=frame[numeric+['mcc']].copy()
            for i,name in enumerate(STAT_NAMES):result['prior_'+name]=statistics[:,i]
            return result
        AX=assemble(X,train);AVX=assemble(VX,context[len(X):])
        names=numeric+['prior_'+name for name in STAT_NAMES]
        prep=ColumnTransformer([('numeric','passthrough',names),
                 ('mcc',OrdinalEncoder(handle_unknown='use_encoded_value',unknown_value=-1,encoded_missing_value=-1),['mcc'])])
        model=Pipeline([('preprocess',prep),('model',HistGradientBoostingClassifier(
            categorical_features=[False]*len(names)+[True],**params))])
        begin=time.monotonic()
        with threadpool_limits(limits=2):model.fit(AX,y)
        train_seconds=time.monotonic()-begin
        begin=time.monotonic()
        with threadpool_limits(limits=2):scores=model.predict_proba(AVX)[:,1]
        inference_seconds=time.monotonic()-begin
        joblib.dump(model,output/'model.joblib',compress=3)
        sample=np.random.default_rng(975).choice(len(VX),96,replace=False)
        with threadpool_limits(limits=2):replay=joblib.load(output/'model.joblib').predict_proba(AVX.iloc[sample])[:,1]
        difference=float(np.max(np.abs(scores[sample]-replay)))
        if difference>1e-12:raise ValueError('Tree replay differs')
        ranking=Ranking(vmeta,vy);metrics=ranking.metrics(scores,VX.channel.to_numpy())
        np.save(output/'development-scores.npy',scores)
        comparisons={}
        for name in ('flat','statistics','mean','mean-self'):
            baseline=np.load(history_run/name/'ensemble.npy',allow_pickle=False)
            comparisons['tree_vs_'+name]=ranking.compare(baseline,scores)
        result={'status':'posthoc_statistics_tree_complete','metrics':metrics,'comparisons':comparisons,
                'train_seconds':train_seconds,'inference_seconds':inference_seconds,'reload_max_abs_diff':difference,
                'model_bytes':(output/'model.joblib').stat().st_size,'peak_process_bytes':peak_working_set_bytes(),
                'data':loaded[1],'training_ids_sha256':ids,'training_rows':len(X),'training_positives':int(y.sum()),
                'statistic_sha256':{'training':digest(history_run/'training-statistics.npy'),
                                    'inference':digest(history_run/'inference-statistics.npy')},
                'params':params,'posthoc':True,'final_2019_read':False,'paid_calls':0,
                'limit':'Different loss/class balancing and capacity from neural networks; business challenge control, not a matched causal algorithm comparison'}
        write_json(output/'report.json',result)
        print(json.dumps({'status':result['status'],'TP':metrics['tp_daily10'],'AP':metrics['ap']}),flush=True)
        return result
    except Exception as exc:
        write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc)})
        raise
