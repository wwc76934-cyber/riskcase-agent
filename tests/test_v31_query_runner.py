import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from app.evaluation.v31_query_benchmark import build
from app.evaluation.v31_query_protocol import load, prepare
from app.evaluation import v31_query_score
from app.evaluation.v31_query_runner import assert_scripted, run_offline_case, summarize
from tests.test_v31_query_study import ScriptedClient


@pytest.mark.parametrize('candidate',[0,-1])
def test_four_conditions_share_real_local_mcp_and_missing_slots_remain_in_denominator(tmp_path,candidate):
    dataset=tmp_path/'dataset';prereg=tmp_path/'plan'
    pack=build(dataset);prepare(dataset,prereg)
    case_id=pack['cases'][candidate]['case_id']
    rows=asyncio.run(run_offline_case(dataset,prereg,tmp_path/'exercise',case_id,
        scripted_client_factory=lambda condition,stage:ScriptedClient()))
    assert len(rows)==4
    assert all(r['source_transport']=='official_mcp_stdio' and len(r['phases'])==3 for r in rows)
    assert all(r['external_model_requests']==0 for r in rows)
    receipt=summarize(dataset,prereg,rows)
    assert receipt['formal_agent_value_claim'] is False
    assert receipt['conditions']['B0']['recorded_cases']==3
    assert receipt['conditions']['B4']['recorded_cases']==1
    assert receipt['conditions']['B4']['planned_stages']==360
    assert all(pair['point'] is None and not pair['full_sample_complete'] for pair in receipt['paired_contrasts'])
    with pytest.raises(ValueError,match='Duplicate'):
        summarize(dataset,prereg,rows+[rows[0]])
    changed=deepcopy(rows);changed[0]['protocol_sha256']='invalid'
    with pytest.raises(ValueError,match='fingerprint'):
        summarize(dataset,prereg,changed)


def test_provider_client_rejected_before_dispatch():
    class NonScripted:
        def chat(self,*args,**kwargs):
            pytest.fail('Non-scripted transport must never be called')
    with pytest.raises(ValueError,match='scripted clients only'):
        assert_scripted(NonScripted())


def test_preregistration_accepts_checkout_line_endings_but_keeps_empty_denominator(tmp_path):
    dataset=tmp_path/'dataset';prereg=tmp_path/'plan'
    build(dataset);prepare(dataset,prereg)
    protocol=load(prereg/'protocol.json')
    raw=Path(v31_query_score.__file__).read_bytes()
    other=raw.replace(b'\r\n',b'\n') if b'\r\n' in raw else raw.replace(b'\n',b'\r\n')
    protocol['scoring_sha256']=hashlib.sha256(other).hexdigest()
    (prereg/'protocol.json').write_text(json.dumps(protocol),encoding='utf-8')
    result=summarize(dataset,prereg,[])
    assert all(c['recorded_cases']==0 and c['planned_stages']==360 for c in result['conditions'].values())
    assert all(pair['interval'] is None for pair in result['paired_contrasts'])
