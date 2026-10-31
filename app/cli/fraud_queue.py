"""Inspect a frozen queue and prepare every selected alert in one navigation group."""
import argparse
import json
from pathlib import Path

from app.cli.fraud_bridge import queue_audit
from app.cli.release_demo import verify_package,checked_file
from app.investigation.fraud import from_case
from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.review_groups import group_alerts
from app.service.runtime import write_json


def inspect_queue(release):
    release=Path(release).resolve()
    verify_package(release)
    m=json.loads((release/'manifest.json').read_text())
    if m['data_kind']!='IBM_TabFormer_synthetic' or m['labels_read'] is not False or m['daily_budget']!=10:
        raise ValueError('Frozen label-free top10 release required')
    queue_path=checked_file(release,'daily-review.json',m['artifact_sha256']['daily-review.json'])
    rows=json.loads(queue_path.read_text());audit=queue_audit(rows)
    if len(rows)!=m['queue_rows']:raise ValueError('Queue size mismatch')
    p=m['source_provenance'];groups=group_alerts(rows,p['source_facts_sha256'],p['neural_report_sha256'])
    flattened=[r for group in groups for r in group['members']]
    if sorted(flattened,key=lambda r:r['transaction_id'])!=sorted(rows,key=lambda r:r['transaction_id']):
        raise ValueError('Grouping changed alert membership or facts')
    return dict(version=1,source_release_sha256=sha256_file(release/'manifest.json'),
                queue_sha256=sha256_file(queue_path),source_provenance=p,audit=audit,groups=groups,
                contract='navigation_only_per_transaction_evidence_and_approval',
                alerts_preserved=len(flattened),groups_count=len(groups),daily_transaction_budget=10,
                analyst_minutes_saved=None,labels_read=False,real_model_requests=0)


def prepare_group(release,group,cohort_report,output):
    release,cohort_report,output=map(lambda p:Path(p).resolve(),(release,cohort_report,output))
    if output.exists():raise FileExistsError('New output directory required')
    plan=inspect_queue(release)
    selected=next((g for g in plan['groups'] if g['group_id']==group),None)
    if selected is None:raise ValueError('Group absent from verified queue')
    m=json.loads((release/'manifest.json').read_text());p=plan['source_provenance']
    if sha256_file(cohort_report)!=p['cohort_report_sha256']:raise ValueError('Cohort provenance mismatch')
    cohort=json.loads(cohort_report.read_text());source=cohort_report.parent/'dataset/facts.sqlite'
    if cohort['source_csv_fully_scanned'] is not True or sha256_file(source)!=p['source_facts_sha256']:
        raise ValueError('Source provenance mismatch')
    # Original bound alert supplies the already checked model artifact identity;
    # this export does not retrain, select new targets or claim a new score replay.
    original=m['cases'][0]['manifest']
    original_path=checked_file(release,original,m['artifact_sha256'][original])
    base=json.loads(original_path.read_text())['alert']
    if base['model_report_sha256']!=p['neural_report_sha256']:raise ValueError('Model provenance mismatch')
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'started.json',dict(group=group,source_release_sha256=plan['source_release_sha256'],real_model_requests=0))
    catalog={};members=[]
    for row in selected['members']:
        import hashlib
        identity='queue-'+hashlib.sha256((group+'\0'+row['transaction_id']).encode()).hexdigest()[:32]
        alert=dict(base,transaction_id=row['transaction_id'],score=row['score'],day_rank=row['day_rank'],
                   selection='Frozen completed-day top10; navigation grouping only, individual fact cutoff and approval retained.')
        path=export_case(output/'cases'/identity,source_database=source,expected_source_sha256=p['source_facts_sha256'],
                         transaction_id=row['transaction_id'],alert=alert,
                         coverage_start=cohort['coverage']['warmup_start'],coverage_end=cohort['coverage']['end'],
                         full_source_scanned=True,source_report_sha256=p['cohort_report_sha256'])
        snap=from_case(path,identity)
        if (snap['manifest']['user_id'],snap['cutoff'])!=(row['user_id'],row['event_time']):
            raise ValueError('Queue-to-projection identity mismatch')
        catalog[identity]=str(path)
        members.append(dict(case_id=identity,transaction_id=row['transaction_id'],cutoff=snap['cutoff'],
                            projection_sha256=snap['manifest']['projection_sha256']))
    write_json(output/'catalog.json',catalog)
    receipt=dict(contract=plan['contract'],group=selected,prepared_members=members,
                 source_release_sha256=plan['source_release_sha256'],queue_sha256=plan['queue_sha256'],
                 code_sha256={name:sha256_file(Path(__file__).resolve().parents[2]/name) for name in
                              ('app/cli/fraud_queue.py','app/risk/review_groups.py','app/cli/fraud_bridge.py','app/risk/case_snapshot.py')},
                 original_queue_alerts=plan['alerts_preserved'],original_queue_groups=plan['groups_count'],
                 labels_read=False,real_model_requests=0,group_approval_supported=False)
    write_json(output/'group.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','prepare'])
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--group-id');parser.add_argument('--cohort-report',type=Path)
    args=parser.parse_args()
    if args.action=='inspect':
        if args.output.exists():raise FileExistsError('New output directory required')
        plan=inspect_queue(args.release);args.output.mkdir(parents=True,exist_ok=False)
        write_json(args.output/'queue.json',plan)
        print(json.dumps({k:plan[k] for k in ('alerts_preserved','groups_count','daily_transaction_budget','labels_read')},indent=2))
    else:
        if not args.group_id or not args.cohort_report:parser.error('prepare requires --group-id and --cohort-report')
        receipt=prepare_group(args.release,args.group_id,args.cohort_report,args.output)
        print(json.dumps(dict(group_id=args.group_id,prepared_members=len(receipt['prepared_members']),real_model_requests=0)))


if __name__=='__main__':main()
