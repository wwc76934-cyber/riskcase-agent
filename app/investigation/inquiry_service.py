"""Open fraud inquiry; explicit clients are required for every execution."""
from pathlib import Path
from app.investigation.harness import InvestigationService
from app.investigation.inquiry import Source


class InquiryService(InvestigationService):
    def __init__(self,directory,*,strategy='adaptive',execution_mode='offline',store=None):
        super().__init__(directory,Path(__file__).resolve().parents[2]/'inquiry_skills',
                         strategy=strategy,execution_mode=execution_mode,profile='full',store=store)

    def submit(self,key,snapshot,question):
        Source(snapshot)
        return super().submit(key,snapshot,question)
