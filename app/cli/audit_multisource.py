"""Replay the current conclusion gate over saved evidence, without model calls.

This is a derived receipt, not a rerun of the historical model or a rewrite of
its scores. Inputs remain unchanged, including previously accepted bad answers.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from jsonschema import ValidationError
from app.investigation.contracts import digest
from app.investigation.runner import verify
from app.cli.multisource_investigation import render
from app.service.runtime import write_json


def audit(inputs,output):
    output.mkdir(parents=True,exist_ok=False)
    rows=[]; hashes={}
    for directory in inputs:
        path=directory/'results.json'
        raw=json.loads(path.read_text(encoding='utf-8'));hashes[str(path)]=digest(raw)
        for original in raw['records']:
            row=deepcopy(original);result=row['result']
            row['original_status']=result['status'];row['original_score']=deepcopy(row['score'])
            row['source_directory']=str(directory)
            candidate=result.get('answer')
            if candidate:
                try:
                    if any(e['sha256']!=digest(e['result']) for e in result['evidence'].values()):
                        raise ValueError('Evidence digest mismatch')
                    result['validation']=verify(candidate,result['evidence'])
                except (ValueError, ValidationError) as exc:
                    result.update(status='rejected_by_replay',answer=None,proposed_answer=candidate,replay_reason=str(exc))
                    row['score']['task_passed']=False
            rows.append(row)
    source_root=Path(__file__).resolve().parents[2]
    sources=['app/investigation/verifier.py','app/investigation/runner.py','app/investigation/contracts.py','app/cli/audit_multisource.py']
    for name in sources:
        target=output/'source'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((source_root/name).read_bytes())
    write_json(output/'validation-replay.json',dict(real_llm_calls=0,derived_receipt=True,
        source_result_hashes=hashes,source_hashes={n:digest((source_root/n).read_text(encoding='utf-8')) for n in sources},records=rows))
    render(rows,output)
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    rows=audit(args.input,args.output)
    print(json.dumps(dict(cases=len(rows),rejected=sum(r['result']['status']=='rejected_by_replay' for r in rows)),ensure_ascii=False))


if __name__=='__main__': main()
