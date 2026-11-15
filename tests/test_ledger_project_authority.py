"""Shared-interface integration tests: no credentials, provider or real grants."""
from decimal import Decimal
import json
from pathlib import Path
import httpx
import pytest
from app.llm.cost_ledger import CostLedger,BudgetExceeded
from app.llm.siliconflow_client import SiliconFlowClient

ROOT=Path(__file__).resolve().parents[1]
KEY='unit-only-not-a-provider-credential'


def policy(version=2):
    filename='siliconflow-world-v2.json' if version==2 else 'siliconflow-live-v1.json'
    return json.loads((ROOT/'config'/filename).read_text())


class AuthorityLedger:
    def __init__(self,limit='34'):self.limit=limit;self.checked=[];self.reservations=[]
    def validated_project_limit(self,run_id):
        if run_id!='approved-unit-run':raise ValueError('Outside separately approved scope')
        self.checked.append(run_id);return self.limit
    def reserve(self,run_id,amount,**limits):
        self.reservations.append((run_id,amount,limits))
        raise BudgetExceeded('Unit grant exhausted; do not send')


def make(ledger,config):
    calls=[]
    def forbidden(request):calls.append(1);raise AssertionError('A budget-rejected request must never reach transport')
    http=httpx.Client(transport=httpx.MockTransport(forbidden))
    return SiliconFlowClient(KEY,policy=config,ledger=ledger,run_id='approved-unit-run',http_client=http),calls


def test_plain_ledger_keeps_old_28_and_20_caps(tmp_path):
    for version,cap in [(1,'20'),(2,'28')]:
        config=policy(version);config['project_budget_cny']=cap
        instance,_=make(CostLedger(tmp_path/(cap+'.sqlite')),config);instance.close()
        config['project_budget_cny']=str(Decimal(cap)+1)
        with pytest.raises(ValueError,match='authorization'):make(CostLedger(tmp_path/(cap+'.sqlite')),config)


def test_attested_limit_is_scoped_and_still_reserves_before_transport():
    ledger=AuthorityLedger();config=policy();config['project_budget_cny']='34'
    instance,calls=make(ledger,config)
    with pytest.raises(BudgetExceeded):instance.chat([dict(role='user',content='unit test')])
    assert ledger.checked==['approved-unit-run'] and not calls
    assert ledger.reservations[0][2]['project_budget']=='34'
    config['project_budget_cny']='35'
    with pytest.raises(ValueError,match='authorization'):make(ledger,config)
    with pytest.raises(ValueError,match='scope'):
        SiliconFlowClient(KEY,policy=config,ledger=ledger,run_id='another-run')


@pytest.mark.parametrize('limit',['NaN','Infinity','0','-1',True,None])
def test_nonfinite_or_nonpositive_attestations_are_rejected(limit):
    config=policy();config['project_budget_cny']='34'
    with pytest.raises(ValueError):make(AuthorityLedger(limit),config)


def test_authority_does_not_relax_prices_model_or_request_bounds():
    for field,value in [('model','different-model'),('max_requests',7),('input_rate_cny_per_million','.01'),('automatic_retries',1)]:
        config=policy();config.update(project_budget_cny='34',**{field:value})
        with pytest.raises(ValueError):make(AuthorityLedger(),config)
    config=policy(1);config['project_budget_cny']='34'
    with pytest.raises(ValueError):make(AuthorityLedger(),config)


def test_attestation_error_is_not_silently_replaced_with_a_default():
    class MissingGrant(AuthorityLedger):
        def validated_project_limit(self,run_id):raise ValueError('Explicit new grant missing')
    with pytest.raises(ValueError,match='grant'):make(MissingGrant(),policy())
