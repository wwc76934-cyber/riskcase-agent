"""Prepare split/audit only, no parameter optimization or prediction."""
import argparse
import json
from pathlib import Path
import numpy as np
from app.risk.behavior_semantic.study import read_records,static_split,near_audit,write,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--records',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    rows,bad=read_records(a.records);split=static_split(rows)
    np.savez(out/'split.npz',**split)
    membership={r['row_id']:name for name,ids in split.items() for r in [rows[int(i)] for i in ids]}
    write(out/'membership.json',membership)
    report={'status':'split_created_before_training','records_sha256':sha(a.records),'raw_rows':len(rows)+len(bad),'retained_valid_rows':len(rows),
        'dropped_bad_rows':len(bad),'bad_row_ids':[r['row_id'] for r in bad],'split_sha256':sha(out/'split.npz'),
        'membership_sha256':sha(out/'membership.json'),'seed':20261104,'rule':'StratifiedGroupKFold5 fold0=test;remaining StratifiedGroupKFold4 fold0=development;groups=exact ordered sequence hash',
        'counts':{k:{'rows':len(v),'positive':sum(rows[int(i)]['is_fraud'] for i in v)} for k,v in split.items()},
        'multi_event_groups':sum(any(len(g)!=1 for g in r['actions']) for r in rows),
        'near_similarity':near_audit(rows,split),'user_disjoint_verified':False,'temporal_extrapolation':False,'model_training_done':False}
    write(out/'report.json',report);print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':main()
