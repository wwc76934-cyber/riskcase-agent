"""Prepare label-free open inquiries from the SAME frozen fraud alert queue."""
import argparse
from contextlib import closing
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from app.cli.fraud_queue import inspect_queue
from app.cli.release_demo import checked_file
from app.investigation.contracts import digest
from app.investigation.inquiry import Source,CONTRACT
from app.risk.case_snapshot import COLUMNS,local_time
from app.risk.history import sha256_file
from app.service.runtime import write_json

SELECTION_SEED='riskcase-open-inquiry-v1-20261101'
QUESTIONS=[
    ('merchant_persistence','近30天交易次数最多的商户是谁？先找出该商户，再比较它在最近7天与此前23天的交易次数及每日频率。检查是否只是近期集中，说明反证与缺口。商户并列按源标识JSON字典序取第一。'),
    ('card_positive','只看正金额，近30天交易次数最多的是哪张卡？该卡最近24小时与此前29天分别有多少笔？同时给出正金额最大值。比较时注意窗口长度，保留无法验证的授权问题。并列按源标识JSON字典序取第一。'),
    ('merchant_exclusion','找出近30天交易次数最多的商户，排除它后，剩余交易近7天和此前23天各有多少笔、最大正金额是多少？这些结果是否削弱“变化只来自一个商户”的解释？并列按源标识JSON字典序取第一。'),
    ('channel_positive','只看正金额，找出近30天正金额合计最大的渠道，再对这个渠道比较最近7天和此前23天的笔数与最大正金额。渠道或币种不能从名称推断；说明替代解释。并列按源标识JSON字典序取第一。'),
    ('merchant_non_chip','排除Chip渠道后，近30天交易次数最多的商户是谁？分别核查这个商户近7天和此前23天的笔数，持续应用同一渠道过滤。若筛选后没有记录，明确无法确定。并列按源标识JSON字典序取第一。'),
    ('card_amount','近30天最大正金额出现在哪张卡？对该卡分别查询最近24小时、此前29天的笔数与最大正金额，检查高金额解释是否有历史反例。并列按源标识JSON字典序取第一。'),
]


def prepare(release,cohort_report,output):
    release,cohort_report,output=map(lambda p:Path(p).resolve(),(release,cohort_report,output))
    if output.exists():raise FileExistsError('New output directory required')
    queue=inspect_queue(release);provenance=queue['source_provenance']
    if sha256_file(cohort_report)!=provenance['cohort_report_sha256']:raise ValueError('Cohort changed')
    cohort=json.loads(cohort_report.read_text());facts=cohort_report.parent/'dataset/facts.sqlite'
    if cohort['source_csv_fully_scanned'] is not True or sha256_file(facts)!=provenance['source_facts_sha256']:
        raise ValueError('Source facts changed')
    manifest=json.loads((release/'manifest.json').read_text())
    original=manifest['cases'][0]['manifest']
    base=json.loads(checked_file(release,original,manifest['artifact_sha256'][original]).read_text())['alert']
    allrows=[r for g in queue['groups'] for r in g['members']]
    users=sorted({r['user_id'] for r in allrows},key=lambda u:hashlib.sha256((SELECTION_SEED+'\0'+u).encode()).hexdigest())[:len(QUESTIONS)]
    selected=[min((r for r in allrows if r['user_id']==u),key=lambda r:(r['event_time'],r['day_rank'],r['transaction_id'])) for u in users]
    if len(selected)!=len(QUESTIONS):raise ValueError('Six distinct queued subjects required')
    output.mkdir(parents=True,exist_ok=False)
    (output/'snapshots').mkdir()
    write_json(output/'selection.json',dict(seed=SELECTION_SEED,rule='hash subjects, earliest queued transaction per subject; no labels or query outcomes',
               questions=QUESTIONS,selected=selected,source_release_sha256=queue['source_release_sha256'],source_provenance=provenance))
    tasks=[]
    with closing(sqlite3.connect(facts.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        for index,(row,(family,question)) in enumerate(zip(selected,QUESTIONS)):
            target=dict(db.execute('SELECT '+','.join(COLUMNS)+' FROM transactions WHERE transaction_id=?',(row['transaction_id'],)).fetchone())
            if (target['user_id'],target['event_time'])!=(row['user_id'],row['event_time']):raise ValueError('Queue identity changed')
            cutoff=local_time(row['event_time']);start=max(cutoff-timedelta(days=365),local_time(cohort['coverage']['warmup_start']))
            prior=[dict(r) for r in db.execute('SELECT '+','.join(COLUMNS)+' FROM transactions WHERE user_id=? AND event_time>=? AND event_time<? ORDER BY event_time,source_row',
                   (row['user_id'],start.isoformat(),row['event_time']))]
            rows=prior+[target]
            alert=dict(base,transaction_id=row['transaction_id'],score=row['score'],day_rank=row['day_rank'],selection='Existing 2018 completed-day top10; label-blind inquiry subject selection')
            snapshot=dict(task_contract=CONTRACT,case_id=f'inquiry-{index+1}',cutoff=row['event_time'],scope='fraud_inquiry',
                          data_kind='synthetic_development',source_origin='IBM_TabFormer_synthetic',history_start=start.isoformat(),
                          source_facts_sha256=provenance['source_facts_sha256'],source_report_sha256=provenance['cohort_report_sha256'],
                          rows_sha256=digest(rows),target_id=row['transaction_id'],alert=alert,rows=rows)
            source=Source(snapshot)
            path=output/'snapshots'/f'inquiry-{index+1}.json';write_json(path,snapshot)
            # Family annotations remain in the controller; never passed to runtime.
            tasks.append(dict(case_id=snapshot['case_id'],family=family,question=question,snapshot=str(path),
                              snapshot_hash=digest(snapshot),prior_rows=len(source.prior)))
    result=dict(status='open_inquiry_sources_prepared_NOT_agent_effectiveness',tasks=tasks,
                source_kind='IBM_synthetic_previously_observed_2018_development',source_provenance=provenance,
                real_model_requests=0,labels_read=False,independent_business_validation=False)
    write_json(output/'prepared.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,default=Path('runs/release-demo-v1'))
    parser.add_argument('--cohort-report',type=Path,default=Path('data/cohorts/cohort-3bfdbaab486f/build-report.json'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=prepare(args.release,args.cohort_report,args.output)
    print(json.dumps(dict(status=result['status'],tasks=len(result['tasks']),prior_rows=[t['prior_rows'] for t in result['tasks']]),indent=2))


if __name__=='__main__':main()
