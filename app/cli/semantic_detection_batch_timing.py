"""Single bounded timing replay after full independent model audit."""
import argparse
import json


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root',required=True)
    p.add_argument('--run',required=True);p.add_argument('--audit',required=True)
    p.add_argument('--broad-run',default='runs/semantic-v21-b-broad-01');p.add_argument('--output',required=True)
    args=p.parse_args()
    from app.risk.semantic_detection.v2.batch_timing import run
    r=run(args.source_root,args.run,args.audit,args.broad_run,args.output)
    print(json.dumps({'status':r['status'],'models':len(r['models']),'max_abs_diff':r['max_abs_diff']}))


if __name__=='__main__':main()
