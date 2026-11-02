"""Read-only source, business-capacity and category-clustering diagnostics."""
from contextlib import closing
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score,normalized_mutual_info_score
from threadpoolctl import threadpool_limits

from app.risk.semantic_detection.common import digest,write_json
from .representations import industry_title,table_for


def source_paths(root):
    manifest=json.loads((Path(root)/'data/features/features-b9692149f1c1/manifest.json').read_text(encoding='utf8'))
    cohort_path=Path(manifest['cohort_report']);cohort=json.loads(cohort_path.read_text(encoding='utf8'))
    facts=cohort_path.parent/'dataset/facts.sqlite';labels=cohort_path.parent/'dataset/evaluation-labels.sqlite'
    if digest(facts)!=cohort['artifact_sha256']['dataset/facts.sqlite'] or digest(labels)!=cohort['artifact_sha256']['dataset/evaluation-labels.sqlite']:
        raise ValueError('Source integrity changed')
    return facts,labels


def capacity(root,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);facts,labels=source_paths(root)
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('ATTACH DATABASE ? AS evaluation',(labels.resolve().as_uri()+'?mode=ro',))
        rows=db.execute('''SELECT substr(t.event_time,1,10),COUNT(*),SUM(l.is_fraud)
            FROM transactions t JOIN evaluation.labels l ON t.transaction_id=l.transaction_id AND t.split=l.split
            WHERE t.split='validation' AND t.event_time>='2018-01-01' AND t.event_time<'2019-01-01'
            GROUP BY substr(t.event_time,1,10) ORDER BY 1''').fetchall()
    positives=np.array([r[2] for r in rows]);ceiling=int(np.minimum(positives,10).sum())
    report={'status':'daily_capacity_diagnostic_complete','days':len(rows),'positive_rows':int(positives.sum()),
            'oracle_daily10_TP_ceiling':ceiling,'oracle_precision_ceiling':ceiling/(len(rows)*10),
            'days_without_positives':int((positives==0).sum()),'days_over10_positives':int((positives>10).sum()),
            'max_daily_positives':int(positives.max()),'daily_counts':[{'day':d,'rows':n,'positives':p} for d,n,p in rows],
            'top7_days_positive_fraction':float(np.sort(positives)[-7:].sum()/positives.sum()),
            'limit':'Outcome-based mathematical ceiling only, not a deployable baseline or a new primary metric',
            'final_2019_read':False,'optimization':False}
    write_json(output/'report.json',report);return report


def composition(audit,glossary_path,output):
    import pandas as pd
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    cells=pd.read_csv(Path(audit)/'mcc-channel-scores.csv',dtype={'year':str,'mcc':str})
    cells=cells[cells.model=='frozen_residual'][['year','channel','mcc','rows','positives']]
    glossary=json.loads(Path(glossary_path).read_text(encoding='utf8'))
    unknown=cells[~cells.mcc.isin(glossary['entries'])]
    coverage=unknown.groupby(['year','channel']).agg(rows=('rows','sum'),positives=('positives','sum')).reset_index()
    reports=[]
    for channel in sorted(cells.channel.unique()):
        data=cells[cells.channel==channel]
        train=data[data.year.isin(['2016','2017'])].groupby('mcc')[['rows','positives']].sum()
        dev=data[data.year=='2018'].set_index('mcc')[['rows','positives']]
        unseen=set(dev.index)-set(train.index)
        if unseen:raise ValueError('Unseen development cells require a separate support analysis')
        codes=sorted(set(train.index)|set(dev.index));train=train.reindex(codes,fill_value=0);dev=dev.reindex(codes,fill_value=0)
        w_train=train.rows/train.rows.sum();w_dev=dev.rows/dev.rows.sum()
        risk=train.positives/train.rows
        train_rate=float(train.positives.sum()/train.rows.sum());dev_rate=float(dev.positives.sum()/dev.rows.sum())
        standardized=float((w_dev*risk).sum())
        reports.append({'channel':channel,'train_rate':train_rate,'development_rate':dev_rate,
            'development_mix_with_training_cell_rates':standardized,'MCC_mix_total_variation':float(np.abs(w_train-w_dev).sum()/2),
            'composition_component':standardized-train_rate,'within_cell_rate_component':dev_rate-standardized,
            'total_rate_change':dev_rate-train_rate,'development_unseen_cells':len(unseen)})
    report={'status':'MCC_channel_standardization_complete','decomposition':reports,
            'dictionary_unknown_MCC_codes':sorted(unknown.mcc.unique()),'unknown_coverage':coverage.to_dict('records'),
            'definition':'Training-cell-rate reference: total change = MCC-mixture change + residual within-cell-rate change',
            'limit':'Algebraic standardization, not causal identification; residual can include other feature/entity mixtures and label mechanisms',
            'new_training':False,'final_2019_read':False}
    write_json(output/'report.json',report);return report


def cluster(bank,glossary_path,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    glossary=json.loads(Path(glossary_path).read_text(encoding='utf8'))
    sector_names={'Airlines, passenger air transportation','Car rental agencies','Lodging, hotels and motels'}
    results={}
    for dimensions in (32,128,256):
        keys,vectors=table_for(bank,channel=False,dimensions=dimensions)
        selected=[i for i,(m,_) in enumerate(keys) if m in glossary['entries']]
        codes=[keys[i][0] for i in selected];titles=[glossary['entries'][m]['title'] for m in codes]
        table=vectors[selected]
        sectors=[industry_title(m,t) for m,t in zip(codes,titles)]
        brand=np.array([s in sector_names for s in sectors]);labels=np.array(sectors)[brand]
        probes={}
        random=np.random.default_rng(731).normal(size=table.shape);random/=np.linalg.norm(random,axis=1,keepdims=True)
        for name,values in [('qwen',table),('random',random)]:
            with threadpool_limits(limits=2):
                groups=KMeans(n_clusters=3,random_state=731,n_init=20).fit_predict(values[brand])
            probes[name]={'brand_mccs':int(brand.sum()),'ARI':float(adjusted_rand_score(labels,groups)),
                          'NMI':float(normalized_mutual_info_score(labels,groups))}
        with threadpool_limits(limits=2):
            model=KMeans(n_clusters=8,random_state=731,n_init=20).fit(table)
        clusters=[]
        for number in range(8):
            ids=np.flatnonzero(model.labels_==number)
            ordered=ids[np.argsort(np.linalg.norm(table[ids]-model.cluster_centers_[number],axis=1))]
            clusters.append({'cluster':number,'mccs':len(ids),'closest_to_centroid':[
                {'mcc':codes[i],'title':titles[i]} for i in ordered[:6]]})
        results[str(dimensions)]={'brand_sector_probe':probes,'all_known_mcc_clusters8':clusters}
    report={'status':'exploratory_category_cluster_diagnostic_complete','results':results,
            'algorithm':'L2 vectors, Euclidean KMeans; fixed K8 all107 known categories, K3 three verified brand sectors; seed731/n_init20',
            'registered_as':'Additional interpretability diagnostic, no fraud labels or downstream search; not part of28 fraud model runs',
            'fraud_labels_used':False,'transaction_rows_used':False,'paid_calls':0}
    write_json(output/'report.json',report);return report


def raw_sample(root,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);facts,labels=source_paths(root)
    archive=Path(root)/'data/raw/tabformer/e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2.tgz'
    expected='e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2'
    if digest(archive)!=expected:raise ValueError('Pinned original source archive differs')
    wanted={};strata=[]
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        db.execute('ATTACH DATABASE ? AS evaluation',(labels.resolve().as_uri()+'?mode=ro',))
        for year in ('2016','2017','2018'):
            for channel in ('Chip Transaction','Online Transaction','Swipe Transaction'):
                for label in (0,1):
                    rows=db.execute('''SELECT t.source_row,t.user_id,t.card_id,t.event_time,t.channel,t.mcc,t.merchant_id,t.amount_cents,l.is_fraud
                        FROM transactions t JOIN evaluation.labels l ON t.transaction_id=l.transaction_id AND t.split=l.split
                        WHERE t.split IN ('train','validation') AND substr(t.event_time,1,4)=? AND t.channel=? AND l.is_fraud=?
                        ORDER BY t.transaction_id LIMIT 8''',(year,channel,label)).fetchall()
                    strata.append({'year':year,'channel':channel,'label':label,'sampled':len(rows)})
                    for row in rows:wanted[row['source_row']]=dict(row)
    found=0;member_name=None;maximum=max(wanted)
    with tarfile.open(archive,mode='r|gz') as tar:
        for member in tar:
            if member.name!='card_transaction.v1.csv' or not member.isfile():continue
            member_name=member.name;stream=tar.extractfile(member)
            header=next(csv.reader([stream.readline().decode('utf-8-sig')]))
            for number,line in enumerate(stream,start=1):
                if number in wanted:
                    values=next(csv.reader([line.decode('utf8')]))
                    if len(values)!=len(header):raise ValueError('Raw selected record has non-single-line CSV structure')
                    row=dict(zip(header,values));expected_row=wanted[number]
                    if row['Year'].strip() not in {'2016','2017','2018'}:raise ValueError('Selected source date mismatch')
                    hour,minute=map(int,row['Time'].split(':'))
                    timestamp=datetime(int(row['Year']),int(row['Month']),int(row['Day']),hour,minute).isoformat()
                    label={'yes':1,'no':0}.get(row['Is Fraud?'].strip().lower())
                    comparisons={'user_id':row['User'].strip(),'card_id':row['User'].strip()+':'+row['Card'].strip(),
                                 'event_time':timestamp,'channel':row['Use Chip'].strip(),'mcc':row['MCC'].strip(),
                                 'merchant_id':row['Merchant Name'].strip(),
                                 'amount_cents':int(Decimal(row['Amount'].replace('$','').replace(',','').strip())*100),
                                 'is_fraud':label}
                    if any(expected_row[k]!=v for k,v in comparisons.items()):
                        write_json(output/'first-mismatch.json',{'source_row':number,'expected':expected_row,'observed':row})
                        raise ValueError('Independent original CSV fact/label mismatch')
                    found+=1
                if number%5000000==0:print(f'raw-audit opaque-lines-skipped={number} selected={found}/{len(wanted)}',flush=True)
                if number>=maximum:break
            break
    if found!=len(wanted):raise ValueError('Not every selected physical row was found')
    report={'status':'independent_original_CSV_sample_verified','selected':found,'strata':strata,
            'source_archive_sha256':expected,'member':member_name,
            'selected_fields_verified':['user','card','event_time','channel','MCC','merchant','amount','fraud_label'],
            'only_selected_2016_2017_2018_rows_parsed':True,'unselected_record_bytes':'Skipped opaquely, not parsed or evaluated',
            'physical_row_assumption':'All inspected rows match original logical CSV source_row, with exact header field counts',
            'final_2019_read':False,'optimization':False}
    write_json(output/'report.json',report);return report


def raw_label_census(root,output):
    """Verify every selected 2016–18 outcome without decoding other CSV records."""
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    facts,labels=source_paths(root)
    expected_sha='e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2'
    archive=Path(root)/f'data/raw/tabformer/{expected_sha}.tgz'
    if digest(archive)!=expected_sha:raise ValueError('Original archive changed')
    channels=('Chip Transaction','Online Transaction','Swipe Transaction')
    wanted={};expected=np.zeros(18,dtype=np.int64);observed=np.zeros_like(expected)
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('ATTACH DATABASE ? AS evaluation',(labels.resolve().as_uri()+'?mode=ro',))
        cursor=db.execute('''SELECT t.source_row,substr(t.event_time,1,4),t.channel,l.is_fraud
            FROM transactions t JOIN evaluation.labels l ON t.transaction_id=l.transaction_id AND t.split=l.split
            WHERE t.split IN ('train','validation') AND t.event_time>='2016-01-01' AND t.event_time<'2019-01-01' ''')
        for source_row,year,channel,label in cursor:
            key=(int(year)-2016)*6+channels.index(channel)*2+int(label)
            if source_row in wanted:raise ValueError('Duplicate selected physical source row')
            wanted[source_row]=key;expected[key]+=1
    count=len(wanted);maximum=max(wanted);found=0
    with tarfile.open(archive,mode='r|gz') as tar:
        for member in tar:
            if member.name!='card_transaction.v1.csv' or not member.isfile():continue
            stream=tar.extractfile(member);header=next(csv.reader([stream.readline().decode('utf-8-sig')]))
            indices={name:header.index(name) for name in ('Year','Use Chip','Is Fraud?')}
            for number,line in enumerate(stream,start=1):
                if number in wanted:
                    row=next(csv.reader([line.decode('utf8')]))
                    if len(row)!=len(header):raise ValueError('Physical source-row structure differs')
                    year=int(row[indices['Year']]);channel=row[indices['Use Chip']].strip()
                    label={'yes':1,'no':0}.get(row[indices['Is Fraud?']].strip().lower())
                    if year not in (2016,2017,2018) or label is None:raise ValueError('Unexpected selected source year/label')
                    key=(year-2016)*6+channels.index(channel)*2+label
                    if key!=wanted[number]:
                        write_json(output/'first-label-mismatch.json',{'source_row':number,'expected_code':wanted[number],'observed_code':key})
                        raise ValueError('Selected raw outcome/channel/year differs from extracted dataset')
                    observed[key]+=1;found+=1
                if number%5000000==0:print(f'raw-label-census physical_lines={number} matched={found}/{count}',flush=True)
                if number>=maximum:break
            break
    if found!=count or not np.array_equal(expected,observed):raise ValueError('Incomplete raw selected-label census')
    groups=[{'year':2016+y,'channel':c,'rows':int(observed[y*6+i*2:y*6+i*2+2].sum()),
             'positives':int(observed[y*6+i*2+1])} for y in range(3) for i,c in enumerate(channels)]
    result={'status':'all_selected_2016_2018_raw_labels_verified','selected_records':found,'year_channel_counts':groups,
            'source_archive_sha256':expected_sha,'verified_fields':['year','channel','fraud_label'],
            'unselected_record_bytes':'Skipped opaquely; no decoding, including 2019 records',
            'limit':'Verifies all extracted cohort records against source; does not independently prove extraction completeness',
            'final_2019_read':False,'optimization':False}
    write_json(output/'report.json',result);return result
