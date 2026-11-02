"""Isolated, explicitly gated controlled-label experiment commands."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['run','audit','report'])
    parser.add_argument('--source-root',required=True)
    parser.add_argument('--history-run',default='runs/semantic-v21-b-01')
    parser.add_argument('--broad-run',default='runs/semantic-v21-b-broad-01')
    parser.add_argument('--B-result',dest='B_result',default='docs/semantic-detection-v2/history-results.json')
    parser.add_argument('--B-review',dest='B_review',default='docs/semantic-detection-v2/history-review.json')
    parser.add_argument('--policy',default='config/semantic-detection/v21-d.json')
    parser.add_argument('--run',dest='run_directory')
    parser.add_argument('--audit')
    parser.add_argument('--tests-xml')
    parser.add_argument('--training-resources',default='runs/semantic-v21-d-resources-01/run.json')
    parser.add_argument('--audit-resources',default='runs/semantic-v21-d-resources-01/audit.json')
    parser.add_argument('--output',required=True)
    parser.add_argument('--resource-output',required=True)
    args=parser.parse_args()
    resource=Path(args.resource_output)
    if resource.exists():raise FileExistsError('New resource receipt path required')
    started=time.monotonic();cpu=time.process_time();status='failed';error=None
    try:
        if args.command=='run':
            from app.risk.semantic_detection.v2.semi_experiment import run
            result=run(args.source_root,args.history_run,args.broad_run,args.B_result,args.B_review,args.policy,args.output)
        elif args.command=='audit':
            if not args.run_directory:parser.error('--run required')
            from app.risk.semantic_detection.v2.semi_audit import run
            result=run(args.source_root,args.run_directory,args.history_run,args.broad_run,args.output)
        else:
            if not args.run_directory or not args.audit or not args.tests_xml:parser.error('--run, --audit and --tests-xml required')
            from app.risk.semantic_detection.v2.semi_report import run
            result=run(args.source_root,args.run_directory,args.audit,args.history_run,args.broad_run,args.tests_xml,args.output,
                       training_resources=args.training_resources,audit_resources=args.audit_resources)
        status='completed'
        print(json.dumps({'status':result['status'],'output':args.output},ensure_ascii=False))
    except BaseException as exc:
        error={'type':type(exc).__name__,'message':str(exc)};raise
    finally:
        from app.risk.semantic_detection.common import peak_working_set_bytes,write_json
        write_json(resource,{'status':status,'command':args.command,'finished_at_UTC':datetime.now(timezone.utc).isoformat(),
            'wall_seconds':time.monotonic()-started,'process_CPU_seconds_all_threads':time.process_time()-cpu,
            'whole_process_peak_bytes':peak_working_set_bytes(),'error':error,
            'timing_scope':'CLI main including experiment imports; CPU sums all threads; memory is cumulative whole-process peak'})


if __name__=='__main__':main()
