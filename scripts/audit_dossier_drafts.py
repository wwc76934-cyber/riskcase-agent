"""Retrospective host checks on saved drafts; no response replay or model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from app.evaluation.dossier_cases import build
from app.investigation.contracts import digest
from app.investigation.dossier import verify, DraftReviewError


def main():
    source=Path('runs/stage5-live-v2');target=Path('runs/stage5-draft-audit-v3')
    target.mkdir(exist_ok=False)
    snapshots={c['id']:c['snapshot'] for c in build()}
    records=[]
    for path in sorted(source.glob('raw-*/all_context/result.json')):
        result=json.loads(path.read_text(encoding='utf-8'));snapshot=snapshots[path.parent.parent.name]
        docs={d['id']:d for d in snapshot['documents']}
        evidence=deepcopy(result['evidence'])
        for record in evidence.values():
            payload=record['result']
            if digest(payload)!=record['sha256']:raise ValueError('Original evidence changed')
            originals=payload['data']['documents'] if payload['source']=='policy' else [payload['data']] if payload['source']=='raw_document' else []
            for original in originals:
                doc=docs[original['id']]
                if digest(doc['body'])!=original['content_hash']:raise ValueError('Original body differs; cannot annotate role')
                original['role']=doc['role'];original['review_rule']=doc.get('review_rule')
            record['sha256']=digest(payload)
        issues=[]
        try:verify(result['answer'],evidence,purchase_cents=snapshot['purchase_cents'],support_contract=True)
        except DraftReviewError as exc:issues=exc.issues
        records.append(dict(case=snapshot['case_id'],original_result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            original_verdict=result['answer']['verdict'],new_host_issues=issues))
    audit=dict(kind='retrospective_support_contract_check_NOT_NEW_MODEL',new_model_requests=0,
               transformation='Added host catalog source roles and bounded policy-check metadata to copies only; verified every original body hash. Original model did not see new metadata/prompt.',
               records=records)
    (target/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(drafts=len(records),flagged=sum(bool(r['new_host_issues']) for r in records),new_model_requests=0)))


if __name__=='__main__':main()
