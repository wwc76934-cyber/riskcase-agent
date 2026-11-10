"""One-shot V3 score and paired analysis after a recorded user approval."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from app.risk.behavior_semantic.study import event_key, read_records, write
from scripts.behavior_v3.analyze_development import cluster_interval
from scripts.behavior_v3.review_blind_freeze import PROPOSAL_SHA, read, verify


def main():
    parser=argparse.ArgumentParser()
    for name in ('proposal','approval','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    approval=read(args.approval)
    if (approval.get('status')!='explicit_user_approval' or approval.get('proposal_sha256')!=PROPOSAL_SHA
            or not approval.get('user_message_reference')):
        raise ValueError('Explicit user approval bound to the proposal is required')
    proposal,_=verify(args.proposal)
    args.output.mkdir(parents=True,exist_ok=False)
    lock=Path('runs/track-a-development-reports-01/v3-final-attempt.lock')
    with lock.open('x',encoding='utf8') as stream:
        json.dump({'approval':approval,'output':str(args.output)},stream,indent=2)
    decision={**proposal,'status':'all_development_choices_frozen_before_final_test','approval':approval}
    approved=args.output/'approved-decision.json'
    write(approved,decision)
    scored=args.output/'scored'
    with (args.output/'scoring.log').open('x',encoding='utf8') as log:
        process=subprocess.run([sys.executable,'-B','-m','scripts.behavior_v3.final_evaluate','--decision',str(approved),
                                '--output',str(scored)],stdout=log,stderr=subprocess.STDOUT,check=False)
    if process.returncode:
        write(args.output/'failure.json',{'scoring_exit_code':process.returncode,'retry_automatically':False})
        raise SystemExit(process.returncode)
    if read(scored/'results.json')['instances']!=35:
        raise ValueError('Incomplete scoring receipt')
    rows,_=read_records(proposal['records'])
    with np.load(proposal['split'],allow_pickle=False) as split:
        test=[rows[int(i)] for i in split['test']]
    y=np.array([r['is_fraud'] for r in test])
    groups=[event_key(r) for r in test]
    comparisons={}
    for contrast in proposal['primary_contrasts']:
        candidate,control=contrast.split('_minus_')
        left=np.load(scored/(control+'-ensemble-test.npy'),allow_pickle=False)
        right=np.load(scored/(candidate+'-ensemble-test.npy'),allow_pickle=False)
        summary=cluster_interval(y,left,right,groups)
        summary['limit']='Exact sequence clusters are not users. Static holdout with 18 positive labels and residual linkage risk.'
        k=proposal['test_size_from_frozen_aggregate']['rows']
        k=(k+99)//100
        tie=np.random.default_rng(20261104).random(len(y))
        ltop=np.lexsort((tie,-left))[:k]
        rtop=np.lexsort((tie,-right))[:k]
        left_tp,right_tp=set(ltop[y[ltop]==1]),set(rtop[y[rtop]==1])
        summary.update(topK_new_positive=len(right_tp-left_tp),topK_displaced_positive=len(left_tp-right_tp),
                       topK_net_positive=len(right_tp)-len(left_tp))
        comparisons[contrast]=summary
    write(args.output/'paired-final.json',{'comparisons':comparisons,'post_test_tuning_allowed':False})


if __name__=='__main__':
    main()
