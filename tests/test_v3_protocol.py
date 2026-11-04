import pytest
from app.evaluation.v3_tasks import build as data
from app.evaluation.v3_human_review import build as review,load,write
from app.evaluation.v3_protocol import prepare

def test_protocol_cannot_be_frozen_without_actual_review_and_never_grants_model_execution(tmp_path):
    dataset=tmp_path/'data';data(dataset);pack=tmp_path/'review';review(dataset,pack)
    with pytest.raises(ValueError,match='human attestation'):prepare(dataset,pack,pack/'review-form.json',tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()
    form=load(pack/'review-form.json');form['reviewer']=dict(name='UNIT fake reviewer, not human approval',background='Unit fixture only',human=True,not_an_author=True)
    for d in form['decisions']:d.update(status='accepted',notes='UNIT substantive notes, not an actual review')
    response=tmp_path/'response.json';write(response,form)
    p=prepare(dataset,pack,response,tmp_path/'protocol')
    assert len(p['cases'])==40 and len(p['schedule'])==600 and p['repeats']==3
    assert set(p['conditions'])=={'B0','B1','B1_staged','B2','B4'} and not p['memory']['enabled']
    assert p['external_model_requests']==0 and not p['paid_execution_authorized'] and not p['agent_value_claim']
    with pytest.raises(FileExistsError):prepare(dataset,pack,response,tmp_path/'protocol')
