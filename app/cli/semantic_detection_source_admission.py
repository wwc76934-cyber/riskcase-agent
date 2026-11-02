"""Reproduce the metadata-only C1 decision without opening candidate data or labels."""
import argparse
import hashlib
import json
from pathlib import Path

from app.risk.semantic_detection.v2.data_admission import decide


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args=parser.parse_args()
    raw=args.evidence.read_bytes();evidence=json.loads(raw)
    if evidence.get('stage')!='Gate_C_source_feasibility_preparation':
        raise ValueError('This command only performs source preparation, not model admission after B')
    decisions=[decide(record,gate_B_verified=False) for record in evidence['sources']]
    args.output.mkdir(parents=True,exist_ok=False)
    result={'stage': evidence['stage'], 'scope':'public source/code/terms metadata only',
        'input_evidence_sha256':hashlib.sha256(raw).hexdigest(), 'decisions':decisions,
        'new_candidate_rows_or_evaluation_labels_opened':0,'new_model_experiments':0,
        'candidate_artifacts_verified':False,'default_detector_changed':False}
    (args.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output/'source-evidence.json').write_bytes(raw)
    print(json.dumps({'output':str(args.output),'sources':len(decisions),'admitted_experiments':0}))


if __name__=='__main__': main()
