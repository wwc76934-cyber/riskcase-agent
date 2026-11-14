"""Product entry for raw cases; small dossiers default to the simpler workflow."""
import json
from pathlib import Path
from app.investigation.dossier import selected
from app.investigation.harness import InvestigationService


class DossierService(InvestigationService):
    def __init__(self,directory,*,strategy='all_context',execution_mode='offline',store=None):
        super().__init__(directory,Path(__file__).resolve().parents[2]/'dossier_skills',
                         strategy=strategy,execution_mode=execution_mode,store=store)

    def submit(self,key,snapshot,question):
        if snapshot.get('task_contract')!='raw-dossier-v1':raise ValueError('Raw dossier required')
        amount=snapshot.get('purchase_cents')
        if type(amount) is not int or not 0<amount<=100000000:raise ValueError('Analyst purchase amount required')
        docs=snapshot['documents']
        if not 1<=len(docs)<=80 or len({d['id'] for d in docs})!=len(docs):raise ValueError('Bounded unique documents required')
        if len(json.dumps(snapshot,ensure_ascii=False).encode())>100000:raise ValueError('Snapshot size limit')
        visible=selected(snapshot)
        if self.strategy=='all_context' and sum(d['kind']!='policy' for d in visible.values())>10:
            raise ValueError('All-context source budget exceeded; explicitly choose an evaluated retrieval workflow')
        return super().submit(key,snapshot,question)
