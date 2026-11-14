"""Streaming client that masks personal identifiers in outgoing prompts and records injection findings.

Opt-in: this client is not the default. It changes only the text sent to the provider; stored
evidence is unchanged. Masked digits keep the JSON valid. Findings are kept per request in
``privacy_audit`` and never block a request, because the Policy Guard does not rely on them.
"""
from copy import deepcopy

from app.investigation.v3.gate4_stream import Gate4StreamingClient
from app.investigation.v3.pii import mask
from app.investigation.v3.untrusted_text import screen


class Gate4ScreenedStreamingClient(Gate4StreamingClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.privacy_audit = []

    def request_payload(self, messages, tools=None):
        cleaned = deepcopy(messages)
        masked = dict(mobile=0, resident_id=0, card=0, email=0)
        findings = set()
        for message in cleaned:
            content = message.get("content")
            if isinstance(content, str):
                message["content"], counts = mask(content)
                for key, value in counts.items():
                    masked[key] += value
                if message.get("role") != "system":
                    findings.update(screen(content))
        self.privacy_audit.append(dict(masked=masked, injection_findings=sorted(findings)))
        return super().request_payload(cleaned, tools)
