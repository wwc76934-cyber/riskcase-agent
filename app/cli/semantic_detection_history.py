"""Run the independent bounded causal history experiment, no semantic combination."""
import argparse
from pathlib import Path
from app.risk.semantic_detection.history_experiment import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source-root', 'cache', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=Path('config/semantic-detection/e2-history-v1.json'))
    args = parser.parse_args()
    run(args.source_root, args.cache, args.protocol, args.output)


if __name__ == '__main__':
    main()
