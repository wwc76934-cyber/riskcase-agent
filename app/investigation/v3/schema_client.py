"""Opt-in schema wire format, with the existing complete-payload cost boundary.

Provider/model support remains unverified; there is no live factory, credential
lookup, silent JSON-mode fallback or retry. Host provenance checks still apply.
"""
from copy import deepcopy
import json
from jsonschema import Draft202012Validator,SchemaError
from app.llm.siliconflow_client import SiliconFlowClient
from app.llm.glm_client import ProtocolError


def delivery_format(messages, tools=None):
    if tools:raise ProtocolError('schema_delivery_requires_v2_without_tools')
    try:
        if not messages or messages[0]['role']!='system':raise ValueError()
        host=json.loads(messages[0]['content'])
        if host['stage'] not in ('extract','report'):raise ValueError()
        schema=host['schema']
        if not isinstance(schema,dict) or schema.get('type')!='object' or schema.get('additionalProperties') is not False:
            raise ValueError()
        Draft202012Validator.check_schema(schema)
        # Do not permit remote schema resolution or network data access.
        def check(value):
            if isinstance(value,dict):
                for name,item in value.items():
                    if name in ('$ref','$dynamicRef') and (not isinstance(item,str) or not item.startswith('#')):
                        raise ValueError()
                    check(item)
            elif isinstance(value,list):
                for item in value:check(item)
        check(schema)
    except (ValueError,KeyError,TypeError,IndexError,SchemaError):
        raise ProtocolError('invalid_host_delivery_schema') from None
    return dict(type='json_schema',json_schema=dict(
        name='riskcase_'+host['stage'],strict=True,schema=deepcopy(schema)))


class SchemaDeliveryClient(SiliconFlowClient):
    def request_payload(self, messages, tools=None):
        if self.policy.get('version')!=2:
            raise ProtocolError('schema_delivery_requires_v2_without_tools')
        payload=super().request_payload(messages,tools)
        payload['response_format']=delivery_format(messages,tools)
        return payload
