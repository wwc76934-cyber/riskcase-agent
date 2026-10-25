import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cli.mixed_benchmark import run
from app.cli.mixed_investigation import execute
from app.risk.mixed_investigation import MixedQuestion
from scripts.verify_mixed_benchmark import verify_batch
from tests.test_mixed_pack import frozen
from tests.test_merchant_research import manifest


@pytest.fixture
def batch(frozen, monkeypatch):
    root,pack=frozen; output=root/'runs/verified-batch'
    def process(args, **kwargs):
        value=lambda name:args[args.index(name)+1]
        checks=args[args.index('--checks')+1:args.index('--min-records')]
        execute(root,Path(value('--output')),Path(value('--case-manifest')),MixedQuestion(checks,int(value('--min-records'))),arm=value('--arm'))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('app.cli.mixed_benchmark.subprocess.run',process)
    run(root,pack,output,split='development')
    return root,pack,output


def test_independent_batch_audit_handles_serialized_policy_and_all_slots(batch):
    root,pack,output=batch
    proof=verify_batch(root,pack,output)
    assert proof['expected_slots']==proof['source_sql_verified_slots']==24
    assert proof['published_validated_reports']==24 and not proof['labels_opened']
    assert all(row['passed']==8 for row in proof['counts'].values())


@pytest.mark.parametrize('tamper',['duplicate_slot','receipt','aggregate','policy_values'])
def test_independent_batch_audit_rejects_tampering(batch,tamper):
    root,pack,output=batch
    path=output/'report.json'; report=json.loads(path.read_text(encoding='utf-8'))
    if tamper=='duplicate_slot': report['records'][-1]=report['records'][0]
    elif tamper=='aggregate': report['aggregate']['agent']['passed']=0
    elif tamper=='receipt':
        row=report['records'][0]; receipt=output/f"{row['task_id']}-{row['arm']}.json"
        changed={**row,'status':'changed'};receipt.write_text(json.dumps(changed))
    else:
        summary=Path(report['records'][0]['summary_path'])
        policy=summary.parent/'policy.json';value=json.loads(policy.read_text());value['max_requests']=100
        policy.write_text(json.dumps(value))
    path.write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError): verify_batch(root,pack,output)
