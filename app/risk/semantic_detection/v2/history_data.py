"""Independently bind within-user cards and verify every causal neighborhood edge."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import pandas as pd

from app.risk.semantic_detection.common import digest
from app.risk.semantic_detection.data import load_data


def load_with_cards(source_root):
    loaded,provenance=load_data(source_root)
    manifest=json.loads(Path(provenance['manifest']).read_text(encoding='utf8'))
    cohort_path=Path(manifest['cohort_report']);cohort=json.loads(cohort_path.read_text(encoding='utf8'))
    facts=cohort_path.parent/'dataset/facts.sqlite';labels=cohort_path.parent/'dataset/evaluation-labels.sqlite'
    if digest(facts)!=cohort['artifact_sha256']['dataset/facts.sqlite']:
        raise ValueError('Card identity source hash mismatch')
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        identities=pd.read_sql_query('''SELECT transaction_id,user_id,card_id,event_time,split FROM transactions
            WHERE split IN ('train','validation') AND event_time>='2016-01-01' AND event_time<'2019-01-01' ''',db)
        db.execute('ATTACH DATABASE ? AS evaluation',(labels.resolve().as_uri()+'?mode=ro',))
        availability=db.execute('''SELECT l.split,COUNT(*),SUM(l.available_at IS NOT NULL)
            FROM evaluation.labels l JOIN transactions t ON t.transaction_id=l.transaction_id AND t.split=l.split
            WHERE t.split IN ('train','validation') AND t.event_time>='2016-01-01' AND t.event_time<'2019-01-01'
            GROUP BY l.split''').fetchall()
    if identities.transaction_id.duplicated().any() or identities.card_id.isna().any() or identities.card_id.astype(str).str.strip().eq('').any():
        raise ValueError('Known unique within-user card identities required')
    identities=identities.set_index('transaction_id',verify_integrity=True)
    result=[]
    for X,y,meta in loaded:
        raw=identities.loc[meta.transaction_id]
        for field in ('user_id','event_time','split'):
            if not np.array_equal(raw[field].astype(str).to_numpy(),meta[field].astype(str).to_numpy()):
                raise ValueError('Card/source feature metadata mismatch: '+field)
        meta=meta.copy();meta['card_id']=raw.card_id.astype(str).to_numpy();result.append((X,y,meta))
    provenance=provenance|{'facts_sha256':digest(facts),'card_identity_scope':'(user_id,card_id), never card_id alone',
                          'label_available_at_counts':[{'split':s,'rows':n,'nonnull':a} for s,n,a in availability],
                          'label_time_limit':'Retrospective fully supervised labels; availability not inferred from event timestamps',
                          'base_feature_limit':'Existing point features include user-level prior aggregate counts; new neighbor/statistic features use user-card only'}
    return result,provenance,facts


def verify_neighbors(metadata,neighbors,ages,*,facts=None,training=False,samples=128):
    begin=time.monotonic()
    required={'transaction_id','user_id','card_id','event_time','split'}
    if not required<=set(metadata) or neighbors.shape!=ages.shape or neighbors.shape[0]!=len(metadata):
        raise ValueError('Known aligned identity/time/card fields required')
    if neighbors.ndim!=2 or neighbors.shape[1] not in (6,12,24) or metadata.transaction_id.duplicated().any():
        raise ValueError('Frozen unique neighborhood contract required')
    if metadata[['user_id','card_id']].isna().any().any() or metadata.user_id.astype(str).str.strip().eq('').any() or metadata.card_id.astype(str).str.strip().eq('').any():
        raise ValueError('Known joint identity required')
    parsed=pd.to_datetime(metadata.event_time,format='ISO8601',errors='raise')
    if parsed.isna().any() or parsed.dt.tz is not None or not parsed.lt(pd.Timestamp('2019-01-01')).all():
        raise ValueError('Only known source-local development/training times allowed')
    if training and (not metadata.split.eq('train').all() or not parsed.lt(pd.Timestamp('2018-01-01')).all()):
        raise ValueError('Development nodes forbidden in training graph')
    times=parsed.astype('int64').to_numpy();users=metadata.user_id.astype(str).to_numpy();cards=metadata.card_id.astype(str).to_numpy()
    edges=0;zero=0
    for start in range(0,len(metadata),32768):
        end=min(start+32768,len(metadata));idx=neighbors[start:end];age=ages[start:end]
        if (idx< -1).any() or (idx>=len(metadata)).any() or not np.isfinite(age).all():raise ValueError('Invalid neighbor index/age')
        valid=idx>=0;safe=idx.clip(min=0)
        if (np.diff(valid.astype(np.int8),axis=1)>0).any():raise ValueError('Missing neighbor holes')
        if (age[~valid]!=0).any() or (age[valid]<=0).any():raise ValueError('Strict canonical edge ages required')
        if (valid&(times[safe]>=times[start:end,None])).any():raise ValueError('Future or equal-time edge')
        if (valid&((users[safe]!=users[start:end,None])|(cards[safe]!=cards[start:end,None]))).any():
            raise ValueError('Cross-user or cross-card edge')
        expected=((times[start:end,None]-times[safe])/1e9).astype(np.float32)
        if not np.array_equal(age[valid],expected[valid]):raise ValueError('Stored edge ages differ from raw timestamps')
        edges+=int(valid.sum());zero+=int((~valid.any(1)).sum())
    sql_count=0
    if facts is not None:
        selected=np.random.default_rng(1217).choice(len(metadata),min(samples,len(metadata)),replace=False)
        ids=metadata.transaction_id.astype(str).to_numpy()
        with closing(sqlite3.connect(Path(facts).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            for i in selected:
                row=metadata.iloc[i]
                scope="split='train'" if training else "split IN ('train','validation')"
                expected=[r[0] for r in db.execute(f'''SELECT transaction_id FROM transactions
                    WHERE user_id=? AND card_id=? AND event_time<? AND event_time>='2016-01-01' AND {scope}
                    ORDER BY event_time DESC,transaction_id DESC LIMIT ?''',
                    (str(row.user_id),str(row.card_id),str(row.event_time),neighbors.shape[1])).fetchall()]
                actual=ids[neighbors[i][neighbors[i]>=0]].tolist()
                if actual!=expected:raise ValueError('Independent SQL last-K mismatch')
                sql_count+=1
    return {'nodes':len(metadata),'edges':edges,'k':neighbors.shape[1],'cold_nodes':zero,
            'all_edges_strict_past_same_user_same_card':True,'independent_SQL_samples':sql_count,
            'training_only':training,'elapsed_seconds':time.monotonic()-begin,
            'node_ids_sha256':hashlib.sha256('\n'.join(metadata.transaction_id).encode()).hexdigest()}
