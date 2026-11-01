"""Isolated V2.1 gated research commands."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['channel-audit','source-audit','prepare-a','geometry','matched','report-a1','embedding-audit','audit-matched','report-a','history'])
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--research-root', default='.')
    parser.add_argument('--output', required=True)
    parser.add_argument('--audit')
    parser.add_argument('--cache', default='runs/semantic-prepare-v2/embedding-cache')
    parser.add_argument('--encoder', default='runs/semantic-detection-models/qwen3-0.6b')
    parser.add_argument('--glossary', default='config/semantic-detection/mcc-glossary.json')
    parser.add_argument('--bank', default='runs/semantic-v21-representations-01')
    parser.add_argument('--policy', default='config/semantic-detection/v21-a.json')
    parser.add_argument('--raw-audit', default='runs/semantic-v21-a1-source-01')
    parser.add_argument('--geometry', default='runs/semantic-v21-geometry-01')
    parser.add_argument('--run')
    parser.add_argument('--matched-audit',default='runs/semantic-v21-a2-audit-01')
    parser.add_argument('--embedding-audit',default='runs/semantic-v21-embedding-audit-01')
    parser.add_argument('--tests-xml',default='runs/semantic-v21-initialization/tests-04.xml')
    parser.add_argument('--gate-a',default='docs/semantic-detection-v2/gate-a-results.json')
    args = parser.parse_args()
    if args.command=='channel-audit':
        from app.risk.semantic_detection.v2.channel_audit import run
        result=run(args.source_root,args.research_root,args.output)
    elif args.command=='source-audit':
        from app.risk.semantic_detection.v2.source_audit import run
        if not args.audit:parser.error('--audit required')
        result=run(args.source_root,args.audit,args.output)
    elif args.command=='geometry':
        from app.risk.semantic_detection.v2.representations import geometry
        result=geometry(args.cache,args.output)
    elif args.command=='history':
        from app.risk.semantic_detection.v2.history_experiment import run
        result=run(args.source_root,args.gate_a,args.policy,args.output)
    elif args.command=='report-a':
        from app.risk.semantic_detection.v2.report import render_gate_a
        if not args.run or not args.audit:parser.error('--run and --audit required')
        result=render_gate_a(args.run,args.matched_audit,args.embedding_audit,args.audit,args.output,args.tests_xml)
    elif args.command=='audit-matched':
        from app.risk.semantic_detection.v2.audit_matched import run
        if not args.run:parser.error('--run required')
        result=run(args.source_root,args.run,args.output)
    elif args.command=='embedding-audit':
        from app.risk.semantic_detection.v2.embedding_audit import run
        result=run(args.bank,args.glossary,args.output)
    elif args.command=='report-a1':
        from app.risk.semantic_detection.v2.report import render_a1
        if not args.audit:parser.error('--audit required')
        result=render_a1(args.audit,args.raw_audit,args.geometry,args.research_root,args.output)
    elif args.command=='matched':
        from app.risk.semantic_detection.v2.experiment import run
        result=run(args.source_root,args.bank,args.policy,args.output)
    else:
        from app.risk.semantic_detection.data import load_data
        from app.risk.semantic_detection.v2.representations import prepare
        (X,_,_),=load_data(args.source_root,train_only=True)[0]
        fields=X[['mcc','channel']].copy();del X
        result=prepare(fields,args.glossary,args.encoder,args.cache,args.output)
    print(json.dumps({'status':result.get('status','complete'),'elapsed_seconds':result.get('elapsed_seconds')}),flush=True)


if __name__ == '__main__':
    main()
