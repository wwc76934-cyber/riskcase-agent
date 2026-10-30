"""Post-run diagnostic only: recover raw proposals without rewriting trial results."""
import hashlib
import json
from pathlib import Path
from decimal import Decimal
from jsonschema import validate
from app.agent.structured_output import parse_model_json
from app.investigation.contracts import ANSWER
from app.investigation.runner import verify
from app.service.runtime import write_json


def main():
    root=Path(__file__).resolve().parents[1];source=root/'runs/stage4-live-v1'
    output=root/'runs/stage4-live-v1-audit';output.mkdir(exist_ok=False)
    cases={c['id']:c for c in json.loads((root/'runs/stage4-freeze-v1/scenarios.json').read_text(encoding='utf-8'))}
    records=json.loads((source/'results.json').read_text(encoding='utf-8'));rows=[];hashes={}
    for record in records:
        path=source/record['case']/record['arm']/'result.json';hashes[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
        result=json.loads(path.read_text(encoding='utf-8'));checkpoint=path.with_name('checkpoint.json')
        state=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {}
        raw=result.get('repair_raw_response') or result.get('raw') or result.get('last_raw_response') or {}
        content=raw.get('content') or '';answer=None;strict=False;extract='unavailable'
        try:
            answer,_=parse_model_json(content);validate(answer,ANSWER);strict=True;extract='existing_parser'
        except Exception:
            for index,char in enumerate(content):
                if char!='{':continue
                try:
                    candidate,_=json.JSONDecoder().raw_decode(content[index:]);validate(candidate,ANSWER)
                    answer=candidate;extract='diagnostic_embedded_JSON_only';break
                except Exception:continue
        validation=None
        if answer:
            try:verify(answer,result.get('evidence',{}));validation='supported'
            except Exception as exc:validation=str(exc)
        rows.append(dict(case=record['case'],arm=record['arm'],original_status=record['status'],original_passed=record['score']['task_passed'],
            original_proposed_correct_metric=record['score']['proposed_verdict_correct'],
            proposal=answer,extraction=extract,strict_parser_accepted=strict,
            raw_proposal_correct=bool(answer and answer['verdict']==cases[record['case']]['gold']['verdict']),
            verification=validation,plan=state.get('plan'),knowledge_queries=record['knowledge_queries'],
            caveat='Diagnostic parse does not complete original run, repair model, or prove post-fix runtime success'))
    write_json(output/'diagnostics.json',rows)
    write_json(output/'source-sha256.json',hashes)
    costs=dict(real_requests=sum(r['real_requests'] for r in records),estimated_cny=str(sum(Decimal(r['estimated_cny']) for r in records)),
        unknown_cost_requests=sum(r['unknown_cost_requests'] for r in records),maximum_runs=12,maximum_requests=72,batch_ceiling_cny='2',
        project_committed_estimate_cny=str(Decimal('8.973584')+sum(Decimal(r['estimated_cny']) for r in records)),provider_invoice_verified=False)
    write_json(output/'costs.json',costs)
    print(json.dumps(costs));print([(r['case'],r['arm'],r['extraction'],r['raw_proposal_correct'],r['verification']) for r in rows])


if __name__=='__main__':main()
