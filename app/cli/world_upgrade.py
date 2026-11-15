"""Bounded research upgrade: proposal, frozen phases, execution and feedback."""
import argparse
import json
from pathlib import Path
from app.evaluation.world_model_runner import propose,prepare_phase,execute_phase,publish_development_feedback,prior_episode


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['propose','prepare','run','feedback'])
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--dataset',type=Path);p.add_argument('--phase')
    p.add_argument('--group',choices=['development','main_eval','memory_eval'])
    p.add_argument('--cases',nargs='+');p.add_argument('--conditions',nargs='+')
    p.add_argument('--live',action='store_true')
    args=p.parse_args()
    if args.action=='propose':
        if not args.dataset:p.error('--dataset required')
        result=propose(args.dataset,args.directory)
    elif args.action=='prepare':
        if not all((args.phase,args.group,args.cases,args.conditions)):p.error('phase, group, cases and conditions required')
        raw=prior_episode(args.directory) if 'raw_history' in args.conditions else None
        result=prepare_phase(args.directory,args.phase,args.group,case_ids=args.cases,conditions=args.conditions,prior_episode=raw)
    elif args.action=='run':
        if not args.phase:p.error('--phase required')
        result=execute_phase(args.directory,args.phase,live=args.live)
    else:
        if not args.phase:p.error('--phase required')
        result=publish_development_feedback(args.directory,args.phase)
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','feedback')},ensure_ascii=False))


if __name__=='__main__':main()
