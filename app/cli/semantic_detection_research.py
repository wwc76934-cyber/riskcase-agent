"""Independent challenger research; never changes the registered detector or Agent."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--source-root', type=Path, required=True)
    prepare.add_argument('--glossary', type=Path, default=Path('config/semantic-detection/mcc-glossary.json'))
    prepare.add_argument('--encoder', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    e1 = sub.add_parser('e1')
    e1.add_argument('--source-root', type=Path, required=True)
    e1.add_argument('--cache', type=Path, required=True)
    e1.add_argument('--protocol', type=Path, default=Path('config/semantic-detection/e1-additive-v2.json'))
    e1.add_argument('--output', type=Path, required=True)
    e3 = sub.add_parser('e3')
    e3.add_argument('--source-root', type=Path, required=True)
    e3.add_argument('--cache', type=Path, required=True)
    e3.add_argument('--protocol', type=Path, default=Path('config/semantic-detection/e3-additive-v2.json'))
    e3.add_argument('--output', type=Path, required=True)
    export = sub.add_parser('export')
    export.add_argument('--source-root', type=Path, required=True)
    export.add_argument('--run', type=Path, required=True)
    export.add_argument('--variant', choices=['B0', 'B1', 'B2', 'B3', 'B3_shuffled'], default='B3')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--users', type=int, default=2)
    args = parser.parse_args()
    if args.command == 'prepare':
        from app.risk.semantic_detection.experiment import prepare as execute
        execute(args.source_root, args.glossary, args.encoder, args.output)
    elif args.command == 'e1':
        from app.risk.semantic_detection.experiment import run_e1
        run_e1(args.source_root, args.cache, args.protocol, args.output)
    elif args.command == 'e3':
        from app.risk.semantic_detection.semisupervised import run
        run(args.source_root, args.cache, args.protocol, args.output)
    else:
        from app.risk.semantic_detection.adapter import export_challenger
        export_challenger(args.source_root, args.run, args.variant, args.output, args.users)


if __name__ == '__main__':
    main()
