"""Offline skill/harness lifecycle with optional process-exit fault injection."""
import argparse
import json
import os
from pathlib import Path
from app.investigation.fixtures import development_cases
from app.investigation.harness import InvestigationService,HarnessWorker
from app.investigation.tickets import Tickets,MockDestination
from app.service.runtime import write_json


def service(directory):
    return InvestigationService(directory,Path(__file__).resolve().parents[2]/'investigation_skills')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init','worker','status','demo'])
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--case',type=int,default=3,choices=range(1,13))
    p.add_argument('--crash',choices=['response_saved','tool_returned','tool_checkpoint'])
    p.add_argument('--ordinal',type=int,default=1)
    a=p.parse_args();s=service(a.directory)
    if a.action in ('init','demo'):
        task=development_cases()[a.case-1]
        job,_=s.submit('demo-case-'+str(a.case),task['snapshot'],'Investigate merchant change' if a.case==3 else 'Investigate evidence conflict' if a.case==4 else 'Investigate payment alerts')
        write_json(a.directory/'job.json',dict(id=job))
        if a.action=='init':print(job);return
    else:job=json.loads((a.directory/'job.json').read_text(encoding='utf-8'))['id']
    if a.action in ('worker','demo'):
        def hook(point,ordinal):
            if point==a.crash and ordinal==a.ordinal:os._exit(23)
        HarnessWorker(s,hook=hook).step()
    row=s.store.get(job)
    if a.action=='demo' and row['state']=='awaiting_review':
        tickets=Tickets(s.store,MockDestination(a.directory/'mock-destination.sqlite'))
        draft=tickets.draft(job,'演示工单：请分析师核对结构结论和证据；本审批由离线演示模拟。')
        tickets.approve(job,draft['hash'],reviewer='offline-demo-simulated-reviewer')
        first=tickets.submit(job,draft['hash']);second=tickets.submit(job,draft['hash'])
        write_json(a.directory/'demo-receipt.json',dict(mode='offline_scripted_no_real_model',job=job,
                   result=row['result'],ticket_id=first['receipt']['id'],duplicate_submit_same_id=first['receipt']['id']==second['receipt']['id'],
                   state=s.store.load(job),human_approval_simulated=True))
    print(json.dumps(dict(id=job,state=row['state'],reason=row['reason'],result=row['result']),ensure_ascii=False))


if __name__=='__main__':main()
