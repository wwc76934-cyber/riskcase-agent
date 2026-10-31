from copy import deepcopy
import pytest
from app.cli.longmemeval_retrieval import runtime,retrieve,score,date


def example():
    return dict(question_id='hidden_abs',question_type='hidden',question='merchant travel',question_date='2023/05/30 (Tue) 13:15',
        answer='LEAK',answer_session_ids=['old'],haystack_session_ids=['old','future'],
        haystack_dates=['2023/05/29 (Mon) 12:00','2023/06/01 (Thu) 12:00'],
        haystack_sessions=[[dict(role='user',content='merchant travel',has_answer=True)],
                           [dict(role='assistant',content='merchant travel merchant travel',has_answer=False)]])


def test_gold_and_id_are_removed_and_future_filtered_before_scoring():
    record=example();packet=runtime(record);changed=deepcopy(record)
    changed.update(answer='different',answer_session_ids=['future'],question_id='other',question_type='other')
    changed['haystack_sessions'][0][0]['has_answer']=False
    assert runtime(changed)==packet
    for mode in ('bm25','recency'):assert [h['session_id'] for h in retrieve(packet,mode)]==['old']
    with pytest.raises(ValueError):retrieve(dict(packet,answer='leak'),'bm25')


def test_multi_session_coverage_not_confused_with_one_hit_or_abstention():
    result=score([dict(session_id='a',characters=10)],{'a','b'})
    assert result['recall@1']==.5 and result['all_hit@5']==0 and result['mrr']==1
    packet=runtime(example());packet['query']='absentterm'
    assert retrieve(packet,'bm25')==[]
    with pytest.raises(ValueError):score([],set())
    with pytest.raises(ValueError):date('yesterday')


def test_repeated_session_instances_filter_before_dedup_without_gold_lookup():
    record=example();record['haystack_session_ids']=['old','old']
    packet=runtime(record)
    assert len(packet['sessions'])==2
    assert retrieve(packet,'recency')==[dict(session_id='old',characters=len(packet['sessions'][0]['text']))]
    record['question_date']='2023/06/02 (Fri) 12:00'
    assert len(retrieve(runtime(record),'bm25'))==1
