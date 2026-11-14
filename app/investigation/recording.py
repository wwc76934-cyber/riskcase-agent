"""Sanitized request/response capture shared by the product and evaluator."""
from copy import deepcopy
from pathlib import Path
from app.service.runtime import write_json


class RecordedClient:
    def __init__(self,base,directory):
        self.base,self.directory=base,Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        ordinals=[int(p.stem.split('-')[-1]) for p in self.directory.glob('request-*.json') if p.stem.split('-')[-1].isdigit()]
        self.calls=max(ordinals,default=0)
        self.simulation=getattr(base,'simulation',False)

    def chat(self,messages,tools=None):
        self.calls+=1;path=self.directory/f'request-{self.calls:02}.json'
        write_json(path,self.scrub(dict(messages=deepcopy(messages),tools=tools or [])))
        try:
            response=self.base.chat(messages,tools=tools)
        finally:
            capture=getattr(self.base,'last_response_capture',None)
            if capture is not None:write_json(self.directory/f'provider-{self.calls:02}.json',self.scrub(capture))
        write_json(self.directory/f'response-{self.calls:02}.json',self.scrub(response))
        return response

    def scrub(self,value):return self.base.scrub(value) if hasattr(self.base,'scrub') else value
    def snapshot(self):return self.base.snapshot() if hasattr(self.base,'snapshot') else dict(simulation=True,scripted_calls=self.calls)
    def close(self):
        if hasattr(self.base,'close'):self.base.close()
