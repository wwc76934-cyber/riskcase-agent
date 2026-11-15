import pytest
from app.evaluation.v3_tasks import build as candidates
from app.evaluation.v3_human_review import build,validate,load,write,sha

def test_pack_is_not_review_and_pending_or_author_attestation_cannot_pass(tmp_path):
    data=tmp_path/'data';candidates(data);pack=tmp_path/'pack';result=build(data,pack)
    assert result['cases']==40 and not result['independent_person_reviewed'] and not result['formal_frozen']
    assert load(pack/'review-form.json')['reviewer']['human'] is False
    with pytest.raises(ValueError,match='human attestation'):validate(pack,pack/'review-form.json')
    form=load(pack/'review-form.json');form['reviewer']=dict(name='UNIT simulated reviewer NOT a real approval',background='Test fixture',human=True,not_an_author=True)
    write(tmp_path/'response.json',form)
    with pytest.raises(ValueError,match='pending'):validate(pack,tmp_path/'response.json')
    for d in form['decisions']:d.update(status='accepted',notes='UNIT test substantive note, not actual human review')
    write(tmp_path/'response.json',form);receipt=validate(pack,tmp_path/'response.json')
    assert receipt['independent_person_reviewed'] and not receipt['external_expert_reviewed'] and not receipt['formal_frozen']
    form['decisions'][0]['reference_sha256']='changed';write(tmp_path/'response.json',form)
    with pytest.raises(ValueError,match='case changed'):validate(pack,tmp_path/'response.json')

def test_html_has_no_implicit_acceptance_network_or_source_injection(tmp_path):
    data=tmp_path/'data';candidates(data);pack=tmp_path/'pack';build(data,pack)
    html=(pack/'review.html').read_text(encoding='utf-8')
    assert 'fetch(' not in html and 'XMLHttpRequest' not in html and 'innerHTML' not in html and 'localStorage' not in html
    assert html.count('</script>')==1 and 'type="checkbox" checked' not in html
    with pytest.raises(FileExistsError):build(data,pack)
