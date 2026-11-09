import numpy as np
from app.risk.behavior_semantic.semantics import description,history_shuffle


def test_description_is_literal_not_risk_invention():
    text=description('/PROFILE/GETCUSTOMERRESPONSE')
    assert '/PROFILE/GETCUSTOMERRESPONSE' in text and 'retrieve' in text
    assert not any(w in text.lower() for w in ['unauthorized','abnormal','takeover','fraudulent'])


def test_public_dictionary_supports_relative_paths_and_single_names():
    assert 'Original endpoint: VERIFICATION/SENDCODE.' in description('VERIFICATION/SENDCODE')
    assert 'Original endpoint: APPLICATIONS.' in description('APPLICATIONS')


def test_history_shuffle_keeps_target_and_does_not_invent_intervals():
    row={'row_id':'self_authored','actions':[[i] for i in range(10)],'times':list(range(10)),'is_fraud':1}
    result=history_shuffle([row])[0]
    assert sorted(g[0] for g in result['actions'])==list(range(10))
    assert result['actions'][-1]==[9] and result['times']==[0.]*10
    assert row['times']==list(range(10))
