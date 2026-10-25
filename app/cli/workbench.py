"""Single-command local offline demo, with a persistent worker and review page."""

import argparse
import json
from pathlib import Path
import threading

from app.cli.service import settings_from_args
from app.risk.demo_fixture import ensure
from app.service.runtime import ResearchService, Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("runs/workbench-v1"))
    parser.add_argument("--demo-data", type=Path, default=Path("runs/public-demo-data-v1"))
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    public_fixture = args.catalog is None
    if not args.catalog: args.catalog = ensure(args.demo_data)
    settings = settings_from_args(args)
    service, stop = ResearchService(settings), threading.Event()
    def work():
        worker = Worker(service)
        while not stop.is_set():
            if worker.step() is None: stop.wait(.5)
    thread = threading.Thread(target=work, daemon=True, name="riskcase-demo-worker")
    thread.start()
    print(json.dumps({"url": f"http://127.0.0.1:{args.port}", "token_file": str(settings.directory / "auth.token"),
                      "live_enabled": settings.allow_live, "data": "self_authored_fixture" if public_fixture else "registered_catalog",
                      "mode": "Local demo; for worker process isolation use app.cli.service serve and worker separately."}, ensure_ascii=False), flush=True)
    import uvicorn
    from app.service.api import create_app
    try: uvicorn.run(create_app(service), host="127.0.0.1", port=args.port, access_log=False)
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == "__main__": main()
