import json
import sys


def test_semi_cli_creates_nested_resource_receipt_without_running_models(tmp_path,monkeypatch):
    from app.cli.semantic_detection_semi import main
    from app.risk.semantic_detection.v2 import semi_experiment
    monkeypatch.setattr(semi_experiment,'run',lambda *args:{'status':'self_authored_resource_fixture'})
    resource=tmp_path/'new'/'nested'/'run.json'
    monkeypatch.setattr(sys,'argv',['fixture','run','--source-root','SELF_AUTHORED','--output',str(tmp_path/'unused'),'--resource-output',str(resource)])
    main();receipt=json.loads(resource.read_text(encoding='utf8'))
    assert receipt['status']=='completed' and receipt['process_CPU_seconds_all_threads']>=0
    assert receipt['whole_process_peak_bytes']>0
    assert not (tmp_path/'unused').exists()
