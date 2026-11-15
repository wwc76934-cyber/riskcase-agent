"""Outcome scoring consumes gold only AFTER execution; no required action sequence."""
from app.investigation.runner import verify


def score(result,gold):
    answer=result.get('answer') or result.get('proposed_answer') or {}
    evidence=result.get('evidence',{});ids=answer.get('evidence_ids',[])
    refs=bool(ids) and all(key in evidence for key in ids)
    cited=[evidence[key]['result'] for key in ids if key in evidence]
    sources={r['source'] for r in cited}
    if any(r['source']=='catalog' and r['data'].get('kind')=='disposition' and r['data'].get('hits')==[] for r in cited):sources.add('disposition')
    validated=False
    try:verify(answer,evidence);validated=True
    except (ValueError,KeyError,TypeError):pass
    except Exception as exc:
        from jsonschema import ValidationError
        if not isinstance(exc,ValidationError):raise
    missing=answer.get('missing',[])
    gap=gold['gap'];text=' '.join(missing).lower()
    aliases={'history':['history','历史','覆盖'],'settlement':['settlement','reconciliation','清算','对账'],
             'disposition':['disposition','independent','处置','独立']}
    gap_ok=bool(missing) and any(word in text for word in aliases[gap]) if gap else missing==[]
    metrics=dict(proposed_verdict_correct=answer.get('verdict')==gold['verdict'],references_valid=refs,
        required_sources_cited=set(gold['required_sources'])<=sources,missing_material_correct=gap_ok,
        host_validation_passed=validated,completed=result.get('status') in ('completed','completed_validated'))
    metrics['task_passed']=all(metrics.values())
    metrics['prose_entailment']='requires_separate_review'
    return metrics
