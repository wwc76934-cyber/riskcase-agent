"""Read-only representative host consistency failures; not a new score."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parents[3]
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    scope=ROOT/'runs/v33-schema-proposal-v1';live=scope/'live';traces=[]
    for path in sorted((live/'v3-model-records').glob('*/response-02.json')):
        req=path.with_name('request-02.json');request=load(req)
        if json.loads(request['messages'][0]['content'])['stage']!='report':continue
        context=json.loads(request['messages'][1]['content']);report=json.loads(load(path)['content'])
        facts=context['observations'];statement=report['source_statement']
        bad_statement=statement['value']!='unresolved' and not any(
            facts.get(i,{}).get('predicate') in ('customer_authorization','customer_retraction')
            and facts.get(i,{}).get('value')==statement['value'] for i in statement['fact_ids'])
        bad_hypotheses=[h for h in report['investigation']['hypotheses'] if
            (h['status']=='supported' and not h['support']) or (h['status']=='weakened' and not h['counterevidence'])]
        if not bad_statement and not bad_hypotheses:continue
        traces.append(dict(request_path=str(req.relative_to(live)),request_sha256=sha(req),response_sha256=sha(path),
            target=context['target_transaction_id'],observations=facts,source_statement=statement,
            statement_inconsistent_with_selected_observations=bad_statement,hypotheses_without_required_references=bad_hypotheses,
            read_line_handles=context['acquired']['read_line_handles'],
            report_schema_already_valid=True,semantic_truth_validated=False,original_files_modified=False))
    out=ROOT/'docs/evidence/v33-final-v1/host-consistency-traces.json'
    with out.open('x',encoding='utf-8') as stream:json.dump(traces,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(trace_count=len(traces),output=str(out))))
if __name__=='__main__':main()
