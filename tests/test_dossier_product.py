import json
from pathlib import Path
import pytest
from app.cli.dossier_review import prepare,execute
from app.evaluation.dossier_cases import build
from app.evaluation.dossier import FixtureClient,local_connect


def packet(tmp_path):
    case=build()[0]
    case['snapshot']['case_id']='analyst-supplied-example'
    path=tmp_path/'input.json'
    path.write_text(json.dumps(dict(snapshot=case['snapshot'],question=case['question'])),encoding='utf-8')
    return case,path


def test_product_has_no_gold_and_requires_explicit_run(tmp_path):
    case,path=packet(tmp_path);directory=tmp_path/'execution'
    manifest=prepare(path,directory)
    assert manifest['state']=='prepared_no_model_requests' and manifest['strategy']=='all_context'
    assert not list(directory.glob('request-*.json'))
    with pytest.raises(ValueError,match='Explicit --live'):
        execute(directory,live=False,max_estimated_cny='.5')
    result=execute(directory,live=True,max_estimated_cny='.5',client_factory=lambda:FixtureClient(case['gold']),connection_factory=local_connect)
    assert result['state']=='awaiting_review'
    saved=json.loads((directory/'result.json').read_text(encoding='utf-8'))
    assert saved['evaluation_score'] is None and saved['validation']['semantic_validation'] is False
    assert len(list(directory.glob('request-*.json')))==1
    def never():raise AssertionError('Must not reconstruct a client for an ended job')
    second=execute(directory,live=True,max_estimated_cny='.5',client_factory=never)
    assert second['new_model_requests']==0
    assert len(list(directory.glob('request-*.json')))==1


@pytest.mark.parametrize('amount',['0','1.01','NaN','Infinity'])
def test_invalid_cost_cap_precedes_any_network_or_filesystem_access(tmp_path,amount):
    with pytest.raises(ValueError):execute(tmp_path/'missing',live=True,max_estimated_cny=amount)


def test_prepare_rejects_gold_and_non_synthetic_inputs(tmp_path):
    case,path=packet(tmp_path)
    value=json.loads(path.read_text(encoding='utf-8'));value['gold']=case['gold']
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError,match='no gold'):prepare(path,tmp_path/'forbidden')
    assert not (tmp_path/'forbidden').exists()
    value.pop('gold');value['snapshot']['data_kind']='real_personal_data'
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError,match='synthetic'):prepare(path,tmp_path/'personal')


def test_changed_manifest_cannot_send_another_payload(tmp_path):
    _,path=packet(tmp_path);directory=tmp_path/'execution';prepare(path,directory)
    manifest=json.loads((directory/'product.json').read_text(encoding='utf-8'));manifest['input_hash']='tampered'
    (directory/'product.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError,match='changed'):execute(directory,live=True,max_estimated_cny='.5')
    assert not (directory/'execution-policy.json').exists()
