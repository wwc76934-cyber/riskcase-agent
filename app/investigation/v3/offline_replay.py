"""Explicit metadata-only counterfactual, never a new model execution or score."""
from copy import deepcopy
from app.investigation.fraud_world import timeline_event,materialize_timeline,source_record
from app.investigation.contracts import digest


def rebind_saved_timeline(answer,evidence):
    result=deepcopy(answer);changes=[]
    for item in result['timeline']:
        matches=[]
        for identity in evidence:
            for layer in ('A','B'):
                try:
                    event=timeline_event(dict(item,evidence_id=identity,layer=layer),evidence)
                    if layer=='B':content=source_record(evidence,identity,item['source_id'])
                    else:
                        data=evidence[identity]['result']['data']
                        rows=data['A']['rows'] if 'A' in data else data.get('rows',[data['target']] if 'target' in data else [])
                        content=next(r for r in rows if r['transaction_id']==item['source_id'])
                    matches.append((identity,layer,event,digest(content)))
                except (ValueError,KeyError):pass
        if not matches:raise ValueError('Timeline original was never acquired')
        if len({(layer,event,content) for _,layer,event,content in matches})!=1:raise ValueError('Ambiguous original event')
        identity,layer,event,_=next((m for m in matches if m[0]==item['evidence_id']),matches[0])
        if item['evidence_id']!=identity:changes.append(dict(source_id=item['source_id'],old_evidence_id=item['evidence_id'],new_evidence_id=identity,operation='rebind_acquired_original_container'))
        item.update(evidence_id=identity,layer=layer,event_time=event)
    result,hydration=materialize_timeline(result,evidence)
    return result,changes+hydration
