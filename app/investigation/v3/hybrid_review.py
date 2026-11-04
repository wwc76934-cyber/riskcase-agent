"""B4 factual layer: literal machine fields + model customer-source semantics."""
from copy import deepcopy
from app.investigation.v3.typed_review import TypedReviewContract
from app.investigation.v3.rule_workflow import machine_auth_observations


class HybridReviewContract(TypedReviewContract):
    name='v3-hybrid-source-review-v1'

    def extraction_schema(self,ledger):
        schema=super().extraction_schema(ledger);facts=schema['properties']['facts']
        branches=facts['items'].get('anyOf',[])
        branches=[b for b in branches if b['properties']['predicate']['const']=='customer_authorization']
        facts['items']=dict(anyOf=branches) if branches else dict(type='object')
        facts['maxItems']=max(0,12-len(machine_auth_observations(ledger))) if branches else 0
        schema['description']+=' Authentication machine fields are extracted by code; emit only customer interpretations here.'
        return schema

    def extract(self,payload,ledger):
        from app.investigation.v3.output import checked
        checked(payload,self.extraction_schema(ledger))
        coded=machine_auth_observations(ledger)
        extraction=TypedReviewContract(self.target_id).extract(dict(facts=deepcopy(payload['facts'])+coded),ledger)
        extraction['producers']={key:'model_customer_semantics' if i<len(payload['facts']) else 'literal_machine_field'
            for i,key in enumerate(extraction['facts'])}
        return extraction

    def initial_extraction(self,ledger):
        if self.extraction_schema(ledger)['properties']['facts']['maxItems']==0:
            return self.extract(dict(facts=[]),ledger)
        return None

    def context_for(self,extraction,ledger):
        context=super().context_for(extraction,ledger)
        context['observation_producers']=deepcopy(extraction['producers'])
        return context

    def assemble(self,payload,extraction,ledger):
        report=super().assemble(payload,extraction,ledger)
        report['observation_producers']=deepcopy(extraction['producers'])
        return report
