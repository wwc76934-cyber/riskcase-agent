"""Independent raw-facts/labels SQL reconciliation, never loading 2019 records."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd

from app.risk.semantic_detection.common import digest, write_json


def run(source_root, channel_audit, output):
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    root=Path(source_root)
    feature_path=root/'data/features/features-b9692149f1c1/manifest.json'
    feature=json.loads(feature_path.read_text(encoding='utf8'))
    cohort_path=Path(feature['cohort_report'])
    cohort=json.loads(cohort_path.read_text(encoding='utf8'))
    facts=cohort_path.parent/'dataset/facts.sqlite'
    labels=cohort_path.parent/'dataset/evaluation-labels.sqlite'
    if digest(cohort_path)!=feature['cohort_report_sha256'] or digest(facts)!=cohort['artifact_sha256']['dataset/facts.sqlite'] or digest(labels)!=cohort['artifact_sha256']['dataset/evaluation-labels.sqlite']:
        raise ValueError('Independent raw source integrity mismatch')
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('ATTACH DATABASE ? AS evaluation',(labels.resolve().as_uri()+'?mode=ro',))
        # Partition and event-time predicates both apply; no test identities enter frames.
        frame=pd.read_sql_query('''SELECT t.transaction_id,t.user_id,t.card_id,t.event_time,t.channel,l.is_fraud
            FROM transactions t JOIN evaluation.labels l ON t.transaction_id=l.transaction_id AND t.split=l.split
            WHERE t.split IN ('train','validation') AND t.event_time>='2016-01-01' AND t.event_time<'2019-01-01'
            ORDER BY t.transaction_id''',db)
    if frame.transaction_id.duplicated().any() or frame.card_id.isna().any() or frame.user_id.isna().any():
        raise ValueError('Known unique transaction and within-user card identities required')
    frame['year']=frame.event_time.str[:4];frame['month']=frame.event_time.str[:7]
    population=frame.groupby(['year','channel']).is_fraud.agg(['size','sum']).reset_index()
    original=pd.read_csv(Path(channel_audit)/'channel-population.csv',dtype={'year':str})
    merged=population.merge(original,on=['year','channel'],how='outer',validate='one_to_one')
    if not (merged['size'].eq(merged.rows)&merged['sum'].eq(merged.positive)).all():
        raise ValueError('Raw SQL disagrees with feature/label adapter counts')
    months=frame.groupby(['month','channel']).is_fraud.agg(['size','sum']).reset_index()
    months.to_csv(output/'raw-month-channel-counts.csv',index=False)
    concentrations=[]
    for (year,channel),rows in frame.groupby(['year','channel']):
        positive=rows[rows.is_fraud==1]
        for grouping in ('user_id','user_card'):
            columns=['user_id','card_id'] if grouping=='user_card' else ['user_id']
            counts=positive.groupby(columns).size().sort_values(ascending=False)
            top=[]
            for key,count in counts.head(10).items():
                identity='|'.join(map(str,key if isinstance(key,tuple) else (key,)))
                top.append({'entity_alias':hashlib.sha256(('v21-concentration\0'+identity).encode()).hexdigest()[:16],
                            'positives':int(count),'share_of_group_positives':float(count/len(positive))})
            concentrations.append({'year':year,'channel':channel,'grouping':grouping,'positive_rows':len(positive),
                                   'positive_entities':len(counts),'top1_fraction':float(counts.iloc[0]/len(positive)) if len(counts) else None,
                                   'top5_fraction':float(counts.head(5).sum()/len(positive)) if len(counts) else None,'top':top})
    report={'status':'independent_raw_SQL_verified','rows':len(frame),'positives':int(frame.is_fraud.sum()),
            'population_matches_adapter':True,'concentration':concentrations,'source_hashes':{'facts':digest(facts),'labels':digest(labels)},
            'card_identity':'Card IDs are local to user; history keys must be (user_id,card_id), never card_id alone',
            'label_availability':'Only binary event-linked labels present; label adjudication/availability times not proven',
            'final_2019_read':False,'training_or_optimization':False}
    write_json(output/'report.json',report)
    return report
