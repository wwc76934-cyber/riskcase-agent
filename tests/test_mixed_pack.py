import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.cli.mixed_benchmark import aggregate, run
from app.cli.mixed_investigation import execute
from app.risk.history import sha256_file
from app.risk.mixed_investigation import MixedQuestion
from app.risk.mixed_pack import CODE, build, load
from tests.test_merchant_research import manifest


@pytest.fixture
def frozen(tmp_path, manifest):
    actual = Path(__file__).resolve().parents[1]
    root = tmp_path/'project'
    for relative in [*CODE, 'config/siliconflow-live-v1.json']:
        target=root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((actual/relative).read_bytes())
    old=root/'runs/source-merchant-stage-v1/tasks-host-only.json';old.parent.mkdir(parents=True)
    old.write_text('[{"user_id":"old-user"}]')
    source=root/'data/cohorts/cohort-3bfdbaab486f/dataset/facts.sqlite';source.parent.mkdir(parents=True)
    original=manifest.parent.parent/'dataset/facts.sqlite'
    with sqlite3.connect(original) as db: rows=db.execute("SELECT * FROM transactions WHERE user_id='u'").fetchall()
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE transactions(transaction_id TEXT,source_row INTEGER,user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)')
        for u in range(4):
            db.executemany('INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)',[(f'{u}-{r[0]}',u*100+r[1],str(u),*r[3:]) for r in rows])
    cohort=source.parent.parent/'build-report.json'
    cohort.write_text(json.dumps({'artifact_sha256':{'dataset/facts.sqlite':sha256_file(source)},'coverage':{'warmup_start':'2017-12-01T00:00:00','end':'2019-01-01T00:00:00'},'source_csv_fully_scanned':True}))
    detector=root/'runs/ml-v1-90f95014b365';detector.mkdir(parents=True)
    queue=detector/'daily-review.jsonl'
    queue.write_text('\n'.join(json.dumps({'strategy':'hist_gradient_boosting','transaction_id':f'{u}-target','event_time':'2018-02-01T12:00:00','score':.8,'day_rank':1}) for u in range(4)))
    (detector/'report.json').write_text(json.dumps({'final_test_read':False,'artifact_sha256':{'daily-review.jsonl':sha256_file(queue)}}))
    pack=root/'runs/mixed-pack'
    build(root,pack,count=4)
    return root,pack


def test_user_disjoint_questions_gold_and_frozen_code(frozen):
    root,pack=frozen;contract,tasks=load(root,pack)
    assert contract['tasks']==16 and contract['users']==4
    dev={t['user_id'] for t in tasks if t['split']=='development'}
    holdout={t['user_id'] for t in tasks if t['split']=='evaluation'}
    assert len(dev)==len(holdout)==2 and not dev&holdout
    assert all(t['gold'] and len(t['gold'])==len(t['question']['checks']) for t in tasks)
    code=root/CODE[0];code.write_text(code.read_text(encoding='utf-8')+'\n# changed')
    with pytest.raises(ValueError,match='Code changed'): load(root,pack)


def test_pack_tampering_rejected(frozen):
    root,pack=frozen
    path=pack/'tasks-host-only.json';path.write_text('[]')
    with pytest.raises(ValueError,match='Tasks changed'): load(root,pack)


def test_batch_failure_in_denominator_and_no_silent_rerun(frozen,monkeypatch):
    root,pack=frozen;output=root/'runs/batch';calls=[]
    def fake_process(args,**kwargs):
        calls.append(args)
        value=lambda name:args[args.index(name)+1]
        arm=value('--arm')
        if len(calls)==3: raise TimeoutError('fixture interruption')
        checks=args[args.index('--checks')+1:args.index('--min-records')]
        execute(root,Path(value('--output')),Path(value('--case-manifest')),MixedQuestion(checks,int(value('--min-records'))),arm=arm)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('app.cli.mixed_benchmark.subprocess.run',fake_process)
    report=run(root,pack,output,split='development')
    assert report['recorded_slots']==report['expected_slots']==24
    assert report['aggregate']['fixed']['passed']==8 and report['aggregate']['agent']['passed']==7
    assert report['aggregate']['agent']['tasks']==8 and len(calls)==24
    again=run(root,pack,output,split='development')
    assert len(calls)==24 and again['aggregate']==report['aggregate']


def test_aggregate_keeps_missing_paid_request_counts():
    report=aggregate([{'arm':'agent','family':'full','status':'interrupted','real_requests':None}])
    assert report['agent']['tasks']==1 and report['agent']['passed']==0 and report['agent']['unknown_request_slots']==1
