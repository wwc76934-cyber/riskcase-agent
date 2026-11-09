"""Opt-in delivery transport for completed provider responses without content."""
from app.investigation.v3.schema_client import SchemaDeliveryClient
from app.llm.glm_client import ProtocolError


class KnownResponseDeliveryClient(SchemaDeliveryClient):
    def chat(self, messages, tools=None):
        try:
            return super().chat(messages,tools)
        except ProtocolError:
            capture=self.last_response_capture or {}
            audit=self.audit[-1] if self.audit else {}
            message=capture.get('message',{})
            if (audit.get('http_status')==200
                and audit.get('returned_model')==self.model
                and 'usage_cost_upper_estimate_cny' in audit
                and audit.get('error_code')=='Assistant message has no content or tool calls'
                and message.get('role')=='assistant'
                and not message.get('content')
                and not message.get('tool_calls')):
                audit['status']='response_received_empty'
                # This host marker cannot be supplied through the provider message.
                # The journal may replay it, but the report parser always rejects it.
                return dict(role='assistant',content='',known_provider_outcome='empty_content')
            raise
