"""Connect saved neural alerts to the fraud Harness without model API calls."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import time

from app.investigation.fraud import from_case
from app.investigation.fraud_service import FraudService
from app.investigation.harness import HarnessWorker
from app.risk.history import sha256_file
from app.service.runtime import write_json


def queue_audit(rows):
    """Describe capacity occupancy; do not merge cases or claim saved analyst time."""
    if not rows:raise ValueError('Empty queue')
    days=defaultdict(list);seen=set();groups=Counter()
    for row in rows:
        if set(row)!={'transaction_id','user_id','event_time','score','day_rank','tie_hash'}:
            raise ValueError('Unexpected queue fields; labels are not accepted')
        if row['transaction_id'] in seen:raise ValueError('Duplicate queue transaction')
        seen.add(row['transaction_id'])
        t=datetime.fromisoformat(row['event_time'])
        if t.tzinfo or t.year!=2018:raise ValueError('2018 source-local queue required')
        if type(row['score']) not in (float,int) or not math.isfinite(row['score']) or not 0<=row['score']<=1:
            raise ValueError('Invalid score')
        if type(row['day_rank']) is not int:raise ValueError('Integer rank required')
        day=t.date().isoformat();days[day].append(row);groups[(day,row['user_id'])]+=1
    for values in days.values():
        ordered=sorted(values,key=lambda r:(-r['score'],r['tie_hash']))
        if len(values)>10 or [r['day_rank'] for r in ordered]!=list(range(1,len(values)+1)):
            raise ValueError('Queue budget or ordering mismatch')
    return dict(alert_transactions=len(rows),days=len(days),unique_users=len({r['user_id'] for r in rows}),
                user_day_groups=len(groups),repeated_user_day_slots=len(rows)-len(groups),
                max_alerts_one_user_day=max(groups.values()),daily_transaction_budget=10,
                grouping_applied=False,human_minutes_saved=None,
                selection='completed-day ranking; not realtime',labels_read=False)


def prepare(release,output):
    from app.cli.release_demo import verify_package,checked_file
    release=Path(release).resolve();output=Path(output).resolve()
    receipt=verify_package(release)
    origin=json.loads((release/'manifest.json').read_text())
    if origin['data_kind']!='IBM_TabFormer_synthetic' or origin['labels_read'] is not False or origin['real_LLM_requests']!=0:
        raise ValueError('Verified source demonstration required')
    hashes=origin['artifact_sha256']
    queue_path=checked_file(release,'daily-review.json',hashes['daily-review.json'])
    rows=json.loads(queue_path.read_text());audit=queue_audit(rows)
    if audit['alert_transactions']!=origin['queue_rows']:raise ValueError('Queue size mismatch')
    queue={r['transaction_id']:r for r in rows}
    catalog_path=checked_file(release,'catalog.json',hashes['catalog.json'])
    catalog=json.loads(catalog_path.read_text())
    snapshots=[]
    for case_id,name in catalog.items():
        # Catalog paths are never granted to the model and must remain in this release.
        path=Path(name).resolve();relative=path.relative_to(release).as_posix()
        checked_file(release,relative,hashes[relative])
        snapshot=from_case(path,case_id);m=snapshot['manifest'];a=m['alert'];row=queue[a['transaction_id']]
        if (row['user_id'],row['event_time'],row['score'],row['day_rank'])!=(m['user_id'],m['cutoff'],a['score'],a['day_rank']):
            raise ValueError('Detector-to-case identity mismatch')
        if a['model_report_sha256']!=origin['source_provenance']['neural_report_sha256'] or m['source_facts_sha256']!=origin['source_provenance']['source_facts_sha256']:
            raise ValueError('Detector-to-source provenance mismatch')
        snapshots.append(snapshot)
    output.mkdir(parents=True,exist_ok=False)
    service=FraudService(output/'service/investigations')
    entries=[];started=time.monotonic()
    for snapshot in snapshots:
        identity,_=service.submit('bridge-'+snapshot['case_id'],snapshot,'核查此检测告警的三项历史行为线索，保留授权与外部结案证据缺口。')
        row=HarnessWorker(service).step()
        if row['id']!=identity or row['state']!='awaiting_review':raise ValueError('Bridge run did not reach review: '+str(row['reason']))
        checkpoint=service.store.load(identity)
        handshakes=json.loads((service.directory/identity/'mcp-handshakes.json').read_text())
        entries.append(dict(case_id=snapshot['case_id'],job_id=identity,state=row['state'],
                            findings=row['result']['answer']['findings'],verdict=row['result']['answer']['verdict'],
                            model_report_sha256=snapshot['manifest']['alert']['model_report_sha256'],
                            snapshot_sha256=row['spec']['snapshot_hash'],mcp_servers=list(handshakes),
                            tool_calls=len(checkpoint['trace']),fraud_determination=None))
    summary=dict(status='source_linkage_and_offline_mcp_verified_NOT_agent_effectiveness',
                 source_kind='IBM_TabFormer_synthetic',source_release_sha256=sha256_file(release/'manifest.json'),
                 source_receipt=receipt,queue=audit,source_model_rows=origin['validation_rows'],cases=entries,
                 elapsed_seconds=round(time.monotonic()-started,3),real_model_requests=0,
                 client='deterministic_fact_baseline_NOT_LLM',external_real_data_validated=False,
                 approval_performed=False,production_throughput_measured=False)
    write_json(output/'summary.json',summary)
    write_json(output/'catalog.json',catalog)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=prepare(args.release,args.output)
    print(json.dumps(dict(status=result['status'],cases=len(result['cases']),queue=result['queue']),ensure_ascii=False,indent=2))
