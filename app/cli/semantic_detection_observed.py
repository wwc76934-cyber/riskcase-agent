"""Observe total process resources while preserving the research CLI implementation."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

from app.risk.semantic_detection.common import digest, peak_working_set_bytes, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource-output', required=True)
    parser.add_argument('research_arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.research_arguments
    if arguments and arguments[0] == '--': arguments = arguments[1:]
    if not arguments: parser.error('A semantic_detection_v21 command is required')
    output = Path(args.resource_output)
    if output.exists(): raise FileExistsError('Fresh resource receipt required')
    started = datetime.now(timezone.utc).isoformat()
    wall, cpu = time.perf_counter(), time.process_time()
    success, error = False, None
    try:
        from app.cli.semantic_detection_v21 import main as research_main
        sys.argv = ['semantic_detection_v21', *arguments]
        research_main()
        success = True
    except SystemExit as exc:
        success = exc.code in (0, None)
        if not success: error = {'type': 'SystemExit', 'code': exc.code}
        raise
    except BaseException as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    finally:
        write_json(output, {'status': 'completed' if success else 'failed_or_interrupted',
            'started_at_UTC': started, 'finished_at_UTC': datetime.now(timezone.utc).isoformat(),
            'arguments': arguments, 'wall_seconds': time.perf_counter()-wall,
            'process_CPU_seconds_all_threads': time.process_time()-cpu,
            'peak_whole_process_bytes': peak_working_set_bytes(), 'error': error,
            'observer_sha256': digest(__file__),
            'limit': 'Whole-process CPU and peak, including data preparation and training/inference; wall time can include host pauses; not per-model memory'})


if __name__ == '__main__': main()
