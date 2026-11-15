"""Opt-in delivery transport with bounded provider failure evidence."""
import json
import re

import httpx

from app.investigation.v3.schema_client import SchemaDeliveryClient
from app.llm.glm_client import ProtocolError


class KnownResponseDeliveryClient(SchemaDeliveryClient):
    def _capture_rejection(self, response, entry):
        if not 400 <= response.status_code < 600:
            return
        trace = response.headers.get('x-siliconcloud-trace-id', '')
        trace_format = r'(?:ti_)?(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})'
        if re.fullmatch(trace_format, trace) and self.api_key not in trace:
            entry['provider_trace_id'] = trace

        # Only a small, explicitly sized, uncompressed JSON response is useful
        # here. Discard the body after extracting the numeric provider code.
        media_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        encoding = response.headers.get('content-encoding', 'identity').strip().lower()
        length = response.headers.get('content-length', '')
        if (media_type != 'application/json' or encoding != 'identity'
            or not re.fullmatch(r'[0-9]{1,4}', length) or not 0 < int(length) <= min(4096, self.policy['max_response_bytes'])):
            return
        declared_size = int(length)
        try:
            if response.is_stream_consumed:
                body = response.content
            else:
                body = bytearray()
                for chunk in response.iter_raw():
                    if len(body) + len(chunk) > declared_size:
                        return
                    body.extend(chunk)
            if len(body) != declared_size:
                return
            error = json.loads(body)
        except (httpx.HTTPError, httpx.StreamError, ValueError, RecursionError):
            # Diagnostic read failures must retain the original HTTP status.
            return
        code = error.get('code') if isinstance(error, dict) else None
        if type(code) is int and 0 <= code <= 999999:
            entry['provider_error_code'] = code

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
