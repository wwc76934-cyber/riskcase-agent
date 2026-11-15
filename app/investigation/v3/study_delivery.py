"""Common evidence contract and lane-specific transport for the simulation study."""
from copy import deepcopy

from app.investigation.v3.known_response_client import KnownResponseDeliveryClient
from app.investigation.v3.output import checked
from app.investigation.v3.rule_workflow import machine_auth_observations
from app.investigation.v3.schema_client import delivery_format
from app.investigation.v3.selected_fact_report import SelectedFactReportContract
from app.investigation.v3.v31_output import V31ReviewContract
from app.llm.siliconflow_client import SiliconFlowClient


class StudyReviewContract(SelectedFactReportContract):
    name = 'v31-study-review-v1'

    def composite_schema_for(self, ledger):
        possible = {'F'+str(i): dict(predicate='customer_authorization', value='unresolved',
                    transaction_ids=[self.target_id]) for i in range(1, 13)}
        # The final check uses the actual extracted facts. Placeholders here
        # only make prospective F handles expressible in a single response.
        return self.schema_for(dict(facts=possible), ledger)

    def composite_context_for(self, ledger):
        context = self.context_for(dict(facts={}), ledger)
        return {key: context[key] for key in ('report_codebook', 'host_transaction_amount_display')}

    def rules_for(self, stage):
        rules = super().rules_for(stage)
        if stage == 'report':
            rules += ['Use the current evidence and query outcomes to distinguish an unavailable source from an unread source.',
                      'Record material contradictory observations as hypothesis counterevidence. Recommend human investigation, with no financial execution.']
        return rules


class StudyHybridReviewContract(StudyReviewContract):
    name = 'v31-study-hybrid-review-v1'

    def extraction_schema(self, ledger):
        schema = super().extraction_schema(ledger)
        facts = schema['properties']['facts']
        branches = [branch for branch in facts['items'].get('anyOf', [])
                    if branch['properties']['predicate']['const'] != 'authentication']
        facts['items'] = dict(anyOf=branches) if branches else dict(type='object')
        facts['maxItems'] = max(0, facts['maxItems'] - len(machine_auth_observations(ledger))) if branches else 0
        return schema

    def extract(self, payload, ledger):
        checked(payload, self.extraction_schema(ledger))
        model_facts = deepcopy(payload['facts'])
        coded = machine_auth_observations(ledger)
        result = V31ReviewContract(self.target_id).extract(dict(facts=model_facts + coded), ledger)
        result['producers'] = {key: 'model_semantics' if i < len(model_facts) else 'literal_machine_field'
                               for i, key in enumerate(result['facts'])}
        return result

    def initial_extraction(self, ledger):
        if self.extraction_schema(ledger)['properties']['facts']['maxItems'] == 0:
            return self.extract(dict(facts=[]), ledger)
        return None

    def assemble(self, payload, extraction, ledger):
        report = super().assemble(payload, extraction, ledger)
        report['observation_producers'] = deepcopy(extraction['producers'])
        return report


class ReliabilityReviewContract(StudyHybridReviewContract):
    """Observed development gate: delegate customer language, keep machine fields local."""
    name = 'v35-observed-customer-review-v1'

    def extraction_schema(self, ledger):
        schema = super().extraction_schema(ledger)
        facts = schema['properties']['facts']
        branches = [branch for branch in facts['items'].get('anyOf', [])
                    if branch['properties']['predicate']['const'] in ('customer_authorization','customer_retraction')]
        facts['items'] = dict(anyOf=branches) if branches else dict(type='object')
        if not branches:
            facts['maxItems'] = 0
        return schema

    def rules_for(self, stage):
        rules = super().rules_for(stage)
        if stage == 'extract':
            rules += ['Emit only target-payment customer statements and retractions. Literal authentication is host-supplied. Procedure originals can be referenced in the report timeline.']
        return rules


class StudyDeliveryClient(KnownResponseDeliveryClient):
    def request_payload(self, messages, tools=None):
        payload = SiliconFlowClient.request_payload(self, messages, tools)
        if not tools:
            payload['response_format'] = delivery_format(messages, stages=('extract','report','extract_and_report'))
        return payload
