"""Prepare the 30/40/20 multi-source simulated fraud investigation environment."""
import argparse
import json
from pathlib import Path
from app.evaluation.fraud_world_data import build


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,default=Path('runs/release-demo-v1'))
    parser.add_argument('--cohort-report',type=Path,default=Path('data/cohorts/cohort-3bfdbaab486f/build-report.json'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--audit',action='store_true')
    args=parser.parse_args()
    if args.audit:
        from app.evaluation.fraud_world_audit import audit
        from app.service.runtime import write_json
        report=audit(args.output,args.cohort_report)
        destination=args.output/'audit.json'
        if destination.exists():raise FileExistsError('Audit receipt already exists; do not overwrite')
        write_json(destination,report)
        print(json.dumps({k:v for k,v in report.items() if k!='cases'}));return
    report=build(args.release,args.cohort_report,args.output)
    print(json.dumps(dict(status=report['status'],counts=report['counts'],cases=len(report['tasks']),model_calls=0)))


if __name__=='__main__':main()
