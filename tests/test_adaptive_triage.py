import json
from pathlib import Path
import pytest

from app.agent.structured_output import parse_model_json
from app.cli.triage_benchmark import cases
from app.risk.task_evaluation import build_development_pack
from app.risk.adaptive_triage import ORDER, run_code, run_model, score, expected_assessment
from app.tools.case_tools import CaseTools
from app.llm.fake_client import make_tool_call_message


@pytest.mark.parametrize('raw', ['{"a":1}', '```json\n{"a":1}\n```','```\n{"a":1}\n```'])
def test_narrow_presentation_normalization(raw):
    assert parse_model_json(raw)[0] == {'a':1}


@pytest.mark.parametrize('raw', ['prose {"a":1}', '```json\n{"a":1}\n``` extra',
    '```json\n{"a":1,"a":2}\n```', '```json\n{"a":NaN}\n```',
    '```json\n{"a":1}\n```\n```json\n{"b":2}\n```'])
def test_prose_duplicates_and_nonfinite_not_salvaged(raw):
    with pytest.raises(ValueError): parse_model_json(raw)


@pytest.fixture
def pack(tmp_path):
    source=tmp_path/'cases.json';source.write_text(json.dumps(cases(Path(__file__).resolve().parents[1])),encoding='utf-8')
    return build_development_pack(source,tmp_path/'pack')


@pytest.mark.parametrize('case,expected_calls,expected_checks', [
    ('normal_seen',4,3),('large_new',3,2),('burst',2,1),
    ('short_coverage',2,1),('negative_current',3,2),('merchant_only',4,3)])
def test_branch_oracle_and_exhaustive_comparison(pack,case,expected_calls,expected_checks):
    task=next(t for t in pack if t['id']==case)
    tools=CaseTools(Path(task['manifest']),max_calls=4)
    adaptive=run_code(tools)
    assert score(adaptive,task['gold'])['task_passed']
    assert adaptive['tool_attempts']==expected_calls
    assert len(adaptive['findings']['findings'])==expected_checks
    fixed=run_code(CaseTools(Path(task['manifest']),max_calls=4),exhaustive=True)
    assert score(fixed,task['gold'])['answer_correct']
    assert fixed['tool_attempts']==4
    assert score(fixed,task['gold'])['minimal_path']==(expected_calls==4)


class Script:
    """Test-only policy simulator, never reported as model intelligence."""
    def chat(self,messages,tools=None):
        observations=[json.loads(m['content'])['data'] for m in messages if m['role']=='tool' and json.loads(m['content']).get('ok')]
        if not observations:return make_tool_call_message('target','get_alert_transaction',{})
        findings=[]
        for check in ORDER:
            name='query_target_merchant' if check==ORDER[2] else 'summarize_history'
            hours=1 if check==ORDER[0] else 720
            row=next((r for r in observations if r['tool']==name and r.get('lookback_hours')==hours),None)
            if row is None:return make_tool_call_message(check,name,{'lookback_hours':hours})
            status=expected_assessment(check,row)
            findings.append(dict(check=check,assessment=status,evidence_ids=[row['evidence_id']]))
            if status!='not_supported':break
        return {'role':'assistant','content':'```json\n'+json.dumps({'findings':findings})+'\n```'}


def test_model_path_canonicalization_keeps_raw_and_validates_citations(pack):
    for task in pack:
        result=run_model(Script(),CaseTools(Path(task['manifest']),max_calls=4))
        assert score(result,task['gold'])['task_passed']
        assert result['output_mode']=='single_json_fence' and result['raw_answer'].startswith('```json')
        assert result['messages']


def test_schema_correction_is_bounded_logged_and_has_no_tools(pack):
    class BadSchema(Script):
        def chat(self,messages,tools=None):
            response=super().chat(messages,tools)
            if messages[-1]['role']=='user' and 'Presentation/schema' in messages[-1]['content']:
                assert tools==[]
                return response
            if response.get('content'):
                response['content']=response['content'].replace('"assessment"','"status"')
            return response
    task=next(t for t in pack if t['id']=='burst')
    result=run_model(BadSchema(),CaseTools(Path(task['manifest']),max_calls=4))
    assert score(result,task['gold'])['task_passed']
    assert result['format_repair'] and result['client_requests']==4
    assert '"status"' in result['raw_answer'] and '"assessment"' in result['repaired_raw_answer']


def test_valid_shape_wrong_fact_is_never_repaired(pack):
    class WrongFact(Script):
        def chat(self,messages,tools=None):
            response=super().chat(messages,tools)
            if response.get('content'):response['content']=response['content'].replace('"supported"','"not_supported"')
            return response
    task=next(t for t in pack if t['id']=='burst')
    result=run_model(WrongFact(),CaseTools(Path(task['manifest']),max_calls=4))
    assert result['status']=='failed' and not result['format_repair']
    assert result['client_requests']==3 and result['raw_answer']
