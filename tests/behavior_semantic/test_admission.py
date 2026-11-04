import pytest
from app.risk.behavior_semantic.admission import validate_member,require_training_admission


@pytest.mark.parametrize('name',['../x.pkl','/x.pkl','C:/x.pkl','..\\x.pkl'])
def test_reject_traversal(name):
    with pytest.raises(ValueError):validate_member(name,10)


def test_limits_and_closed_unknown_gate():
    assert validate_member('data.pkl',101085533)=='data.pkl'
    with pytest.raises(ValueError):validate_member('x',150000001)
    with pytest.raises(ValueError):require_training_admission({'data_rights_cleared':'unknown'})


def test_symbolic_reader_does_not_execute_pickle_reduce(tmp_path):
    import pickle,subprocess,sys
    from pathlib import Path
    marker=tmp_path/'executed.txt'
    class Payload:
        def __reduce__(self):
            return eval,(f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')",)
    source=tmp_path/'payload.pkl';source.write_bytes(pickle.dumps(Payload(),protocol=4))
    script=Path(__file__).resolve().parents[2]/'scripts/behavior_v3/static_pickle_audit.py'
    result=subprocess.run([sys.executable,'-I','-S','-B',str(script),'--input',str(source),'--output',str(tmp_path/'out')],capture_output=True,timeout=10)
    assert result.returncode!=0
    assert not marker.exists()
