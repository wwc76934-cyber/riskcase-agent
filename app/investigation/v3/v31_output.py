"""Opt-in V3.1 contract. Never reinterpret an old checkpoint or response."""
from copy import deepcopy
from app.investigation.contracts import obj
from app.investigation.v3.typed_review import TypedReviewContract,refs
from app.investigation.v3.output import checked,extract,OutputFailure,PREDICATE_ROLES

VALUES={'authentication':['success','failure','unresolved'],
        'customer_authorization':['authorized','denied','unresolved'],
        'customer_retraction':['authorized','denied','unresolved'],
        'device':['changed','unchanged','alias_only','unresolved'],
        'account_contact':['changed','unchanged','unknown']}

class V31ReviewContract(TypedReviewContract):
    name='v31-selected-source-typed-review-v1'

    def extraction_schema(self,ledger):
        if ledger.target_id!=self.target_id:raise OutputFailure('target_scope')
        branches=[]
        for predicate in [*VALUES,'policy_version']:
            groups={}
            for handle,line in ledger.lines.items():
                if line['source_type'] in PREDICATE_ROLES[predicate] and self.target_id in line['transaction_ids']:
                    groups.setdefault(line['source_id'],[]).append(handle)
            for identity,handles in groups.items():
                properties=dict(predicate=dict(const=predicate),refs=refs(handles,minimum=1,maximum=3))
                if predicate=='policy_version':
                    version=ledger.lines[handles[0]]['version']
                    if type(version) is not int or not 1<=version<=100:continue
                else:properties['value']=dict(enum=VALUES[predicate])
                branch=obj(properties,list(properties));branch['description']='One observation from selected original '+identity
                branches.append(branch)
        return obj(dict(facts=dict(type='array',maxItems=12 if branches else 0,uniqueItems=True,items=dict(anyOf=branches) if branches else dict(type='object'))),['facts'])

    def extract(self,payload,ledger):
        checked(payload,self.extraction_schema(ledger));facts=[];seen=set()
        for i,original in enumerate(payload['facts'],1):
            fact=deepcopy(original);line=ledger.lines[fact['refs'][0]];key=(fact['predicate'],line['source_id'])
            if key in seen:raise OutputFailure('duplicate_source_observation')
            seen.add(key)
            if fact['predicate']=='policy_version':fact['value']=line['version']
            facts.append(dict(id='F'+str(i),transaction_ids=[self.target_id],**fact))
        return extract(dict(facts=facts),ledger)

    def rules_for(self,stage):
        rules=super().rules_for(stage)
        if stage=='extract':
            rules=[r for r in rules if 'Procedures stay' not in r]
            rules+=['Historical observations use context available on arrival, never a later correction.',
                'For policy_version select original line refs ONLY, without a value. Host copies that selected original version metadata, never interprets its procedure text as a version. Cite it only if relevant to your investigation.',
                'For all other predicates emit the declared canonical value. Authentication is not intent; absence of affirmation is not denial; another payment is not the target. Use unresolved only for genuine ambiguity.']
        return rules

    def schema_for(self,extraction,ledger):
        schema=super().schema_for(extraction,ledger)
        recommendation=schema['properties']['investigation']['properties']['recommendations']['items']
        ordinary=deepcopy(recommendation);ordinary['properties']['action']=dict(const='analyst_review')
        if extraction['facts']:
            supported=deepcopy(recommendation)
            actions=supported['properties']['action']['enum'];supported['properties']['action']['enum']=[a for a in actions if a!='analyst_review']
            supported['properties']['fact_ids']['minItems']=1
            recommendation=dict(anyOf=[ordinary,supported])
        else:recommendation=ordinary
        schema['properties']['investigation']['properties']['recommendations']['items']=recommendation
        return schema
