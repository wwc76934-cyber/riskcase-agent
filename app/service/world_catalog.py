"""Trusted, hash-bound A/B-only registration; HTTP never accepts source paths."""
import json
from pathlib import Path
from app.investigation.fraud_world import World
from app.risk.history import sha256_file


def export_catalog(dataset_directory,output):
    from app.evaluation.world_runner import checked_dataset
    from app.service.runtime import write_json
    directory,dataset=checked_dataset(dataset_directory);output=Path(output)
    if output.exists():raise FileExistsError('New catalog required')
    entries={}
    for task in dataset['tasks']:
        path=directory/task['runtime'];world=World(json.loads(path.read_text()))
        entries[world.snapshot['case_id']]=dict(path=str(path.resolve()),sha256=sha256_file(path))
    output.parent.mkdir(parents=True,exist_ok=True)
    write_json(output,dict(contract='fraud-world-catalog-v1',origin='IBM_synthetic_A_plus_separate_simulated_B',entries=entries))
    return output


def configure(parent,path=None):
    parent.world_catalog={}
    if path is None:return
    catalog=json.loads(Path(path).read_text(encoding='utf-8'))
    if set(catalog)!={'contract','origin','entries'} or catalog['contract']!='fraud-world-catalog-v1' or catalog['origin']!='IBM_synthetic_A_plus_separate_simulated_B':
        raise ValueError('Explicit A/B catalog required')
    if not isinstance(catalog['entries'],dict) or not 1<=len(catalog['entries'])<=500:raise ValueError('Bounded catalog required')
    for identity,entry in catalog['entries'].items():
        if set(entry)!={'path','sha256'}:raise ValueError('Only runtime paths and hashes are accepted')
        path=Path(entry['path']).resolve()
        value=dict(path=path,sha256=entry['sha256'])
        snapshot=read_entry(value)
        if snapshot['case_id']!=identity:raise ValueError('Case binding mismatch')
        parent.world_catalog[identity]=value


def read_entry(entry):
    if sha256_file(entry['path'])!=entry['sha256']:raise ValueError('Registered runtime changed')
    snapshot=json.loads(entry['path'].read_text(encoding='utf-8'));World(snapshot)
    return snapshot
