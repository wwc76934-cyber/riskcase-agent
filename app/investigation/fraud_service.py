"""Fraud lead entry reuses the persistent investigation harness."""
from pathlib import Path
from app.investigation.fraud import BoundSource
from app.investigation.harness import InvestigationService


class FraudService(InvestigationService):
    def __init__(self,directory,*,store=None,execution_mode='offline',profile='full'):
        super().__init__(directory,Path(__file__).resolve().parents[2]/'fraud_skills',store=store,execution_mode=execution_mode,profile=profile)

    def submit(self,key,snapshot,question):
        BoundSource(snapshot)
        return super().submit(key,snapshot,question)
