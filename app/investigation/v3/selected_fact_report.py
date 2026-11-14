"""Unmeasured candidate: select a model-extracted position instead of repeating it.

The host copies only the value of the fact explicitly chosen by the model. It
does not choose a source, decide which statement is true, or consult a reference.
"""
from copy import deepcopy
from app.investigation.v3.report_plan import ReportPlanContract
from app.investigation.v3.output import checked


class SelectedFactReportContract(ReportPlanContract):
    name='v31-selected-customer-fact-report-v1'

    def eligible(self,extraction):
        return {identity:f for identity,f in extraction['facts'].items()
            if f['predicate'] in ('customer_authorization','customer_retraction')
            and self.target_id in f['transaction_ids']
            and f['value'] in ('authorized','denied','unresolved')}

    def schema_for(self,extraction,ledger):
        schema=super().schema_for(extraction,ledger)
        statement=schema['properties']['source_statement']
        statement['properties'].pop('value');statement['properties'].pop('fact_ids')
        statement['properties']['selected_fact_id']=dict(enum=[None,*sorted(self.eligible(extraction))])
        statement['required']=['selected_fact_id','rationale']
        statement['description']='Choose one actual customer observation as the current source position, or null when genuinely unresolved. Do not repeat its value. The host copies only the selected observation value; no fact is inferred or repaired.'
        investigation=schema['properties']['investigation']['properties']
        item=investigation['hypotheses']['items'];branches=[]
        for status in item['properties']['status']['enum']:
            properties=dict(status=dict(const=status))
            field={'supported':'support','weakened':'counterevidence'}.get(status)
            if field:
                if not extraction['facts']:continue
                properties[field]=dict(minItems=1)
            branches.append(dict(properties=properties))
        # Add constraints to the existing complete item. Do not repeat every
        # description, reference enum and prose guard in three full branches.
        item['anyOf']=branches
        return schema

    def rules_for(self,stage):
        rules=super().rules_for(stage)
        if stage=='report':rules += [
            'source_statement selects selected_fact_id, not a regenerated value or fact_ids. Choose the applicable target customer observation, including the effect of a later correction. The host never chooses or repairs that selection.',
            'Use null only when the current source position really remains unresolved. Missing independent proof of fraud is not by itself ambiguity in what a customer explicitly stated.',
            'A supported hypothesis must select actual support facts; a weakened hypothesis must select actual counterevidence. Otherwise keep its status open.']
        return rules

    def assemble(self,payload,extraction,ledger):
        checked(payload,self.schema_for(extraction,ledger))
        canonical=deepcopy(payload);statement=canonical['source_statement']
        selected=statement.pop('selected_fact_id')
        statement['value']=self.eligible(extraction)[selected]['value'] if selected is not None else 'unresolved'
        statement['fact_ids']=[selected] if selected is not None else []
        # Use the unchanged parent validator, including extraction hash, source,
        # transaction and recommendation checks. It never sees a guessed value.
        result=ReportPlanContract(self.target_id).assemble(canonical,extraction,ledger)
        result['delivery_contract']=self.name
        result['model_selected_customer_fact_id']=selected
        result['statement_value_copied_from_model_selection']=True
        return result
