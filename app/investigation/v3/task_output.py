"""Opt-in target-level statement assessment; never a fraud or liability verdict."""
from copy import deepcopy
from app.investigation.contracts import obj
from app.investigation.v3.output import DECISION,IDS,TEXT,checked,assemble,OutputFailure


class TaskReportContract:
    name='v3-target-source-statement-v1'
    def __init__(self,target_id):
        if not isinstance(target_id,str) or not target_id:raise ValueError('Host target required')
        self.target_id=target_id
        self.schema=obj({'investigation':deepcopy(DECISION),'source_statement':obj({
            'transaction_id':dict(const=target_id),'value':dict(enum=['authorized','denied','unresolved']),
            'fact_ids':deepcopy(IDS),'rationale':deepcopy(TEXT)},['transaction_id','value','fact_ids','rationale'])},['investigation','source_statement'])

    def assemble(self,payload,extraction,ledger):
        checked(payload,self.schema);report=assemble(payload['investigation'],extraction,ledger)
        statement=payload['source_statement'];facts={f['id']:f for f in report['facts']}
        if self.target_id not in ledger.transaction_ids or ledger.target_id is not None and ledger.target_id!=self.target_id:raise OutputFailure('target_scope')
        if not set(statement['fact_ids'])<=facts.keys():raise OutputFailure('statement_fact_reference')
        if statement['value']!='unresolved' and not any(
            facts[i]['predicate'] in ('customer_authorization','customer_retraction') and facts[i]['value']==statement['value']
            and self.target_id in facts[i]['transaction_ids'] for i in statement['fact_ids']):
            raise OutputFailure('statement_inconsistent_with_extraction')
        report.update(contract='v3-task-report-v1',source_statement=deepcopy(statement),
                      source_statement_is_not_confirmed_fraud_or_legal_authorization=True)
        return report
