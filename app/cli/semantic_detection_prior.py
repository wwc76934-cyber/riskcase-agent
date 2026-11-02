"""Bounded D category-prior supplement; original D32 is never modified."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['run','audit','report'])
    p.add_argument('--source-root',required=True);p.add_argument('--parent',default='runs/semantic-v21-d-01')
    p.add_argument('--parent-audit',default='runs/semantic-v21-d-audit-01');p.add_argument('--broad-run',default='runs/semantic-v21-b-broad-01')
    p.add_argument('--bank',default='runs/semantic-v21-representations-01');p.add_argument('--policy',default='config/semantic-detection/v21-d-prior-addendum.json')
    p.add_argument('--run',dest='run_directory');p.add_argument('--audit');p.add_argument('--output',required=True);p.add_argument('--resource-output',required=True)
    p.add_argument('--training-resources',default='runs/semantic-v21-prior-resources-01/run.json')
    p.add_argument('--audit-resources',default='runs/semantic-v21-prior-resources-01/audit.json')
    args=p.parse_args();resource=Path(args.resource_output)
    if resource.exists():raise FileExistsError('New resource receipt required')
    resource.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();cpu=time.process_time();status='failed';error=None
    try:
        if args.command=='run':
            from app.risk.semantic_detection.v2.prior_experiment import run
            result=run(args.source_root,args.parent,args.parent_audit,args.broad_run,args.bank,args.policy,args.output)
        elif args.command=='audit':
            if not args.run_directory:p.error('--run required')
            from app.risk.semantic_detection.v2.prior_experiment import audit
            result=audit(args.source_root,args.run_directory,args.parent,args.parent_audit,args.broad_run,args.output)
        else:
            if not args.run_directory or not args.audit:p.error('--run and --audit required')
            from app.risk.semantic_detection.v2.prior_report import run
            result=run(args.source_root,args.run_directory,args.audit,args.parent,args.output,args.training_resources,args.audit_resources)
        status='completed';print(json.dumps({'status':result['status'],'output':args.output},ensure_ascii=False))
    except BaseException as exc:error={'type':type(exc).__name__,'message':str(exc)};raise
    finally:
        from app.risk.semantic_detection.common import write_json,peak_working_set_bytes
        write_json(resource,{'status':status,'command':args.command,'finished_at_UTC':datetime.now(timezone.utc).isoformat(),
            'wall_seconds':time.monotonic()-started,'process_CPU_seconds_all_threads':time.process_time()-cpu,
            'whole_process_peak_bytes':peak_working_set_bytes(),'error':error})


if __name__=='__main__':main()
