"""Offline process-death probe. This never loads a key or calls a model API."""
import argparse
import json
import os
from pathlib import Path
from app.investigation.world_service import WorldService
from app.investigation.world_baseline import BaselineProbeClient
from app.investigation.harness import HarnessWorker


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--exit-after-response',action='store_true');args=p.parse_args()
    service=WorldService(args.directory,strategy='all_materials')
    job,_=service.submit('offline-process-recovery',json.loads(args.snapshot.read_text()),'Offline process death and journal replay probe, not model planning evidence')
    def hook(kind,ordinal):
        if args.exit_after_response and kind=='response_saved' and ordinal==1:os._exit(86)
    row=HarnessWorker(service,client_factory=BaselineProbeClient,lease_seconds=30,hook=hook).step()
    if row is None:raise ValueError('No claimable task; wait for lease expiry before recovery')
    state=service.store.load(job);report=row['result'] or {}
    print(json.dumps(dict(job=job,state=row['state'],reason=row['reason'],generation=row['generation'],
                         replayed_responses=report.get('replayed_client_responses'),new_client_requests=report.get('new_client_requests'),
                         simulated=True,external_model_requests=0,turns=state.get('turn'))))


if __name__=='__main__':main()
