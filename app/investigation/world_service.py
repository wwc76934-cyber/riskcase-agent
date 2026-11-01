"""Multi-source fraud cases using the persistent shared Harness."""
from pathlib import Path
from app.investigation.harness import InvestigationService
from app.investigation.fraud_world import World


class WorldService(InvestigationService):
    def __init__(self,directory,*,strategy='adaptive',execution_mode='offline',store=None,memory_mode='none',memory_snapshot=None):
        if memory_mode not in ('none','reviewed','raw_history','stale','wrong'):raise ValueError('Unknown memory condition')
        if memory_snapshot is not None:
            from app.investigation.world_memory import validate_override
            if memory_mode in ('none','reviewed'):raise ValueError('Released methods cannot be supplied by override')
            memory_snapshot=validate_override(memory_snapshot)
        elif memory_mode in ('raw_history','stale','wrong'):raise ValueError('Explicit controlled-memory snapshot required')
        self.world_memory_mode=memory_mode;self.world_memory_override=memory_snapshot
        super().__init__(directory,Path(__file__).resolve().parents[2]/'world_skills',
                         strategy=strategy,execution_mode=execution_mode,profile='full',store=store)

    def submit(self,key,snapshot,question):
        World(snapshot)
        return super().submit(key,snapshot,question)
