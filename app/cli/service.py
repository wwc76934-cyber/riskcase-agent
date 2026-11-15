"""Local API and separate persistent worker. Default mode never calls an LLM."""

import argparse
import json
import os
from pathlib import Path
import secrets
import time

from app.service.runtime import ResearchService, Settings


def settings_from_args(args):
    repo = Path(__file__).resolve().parents[2]
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    token_path = directory / "auth.token"
    try:
        with token_path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32))
    except FileExistsError: pass
    token = token_path.read_text(encoding="ascii").strip()
    catalog_path = args.catalog or repo / "config/service-cases-v1.json"
    entries = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog = {name: (repo / path).resolve() for name, path in entries.items()}
    return Settings(repo, directory, catalog, token, args.allow_live)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "worker", "status"))
    parser.add_argument("--directory", type=Path, default=Path("runs/service-v1"))
    parser.add_argument("--catalog", type=Path, help="Trusted host configuration; never supplied by an HTTP client")
    parser.add_argument("--world-catalog",type=Path,help="Trusted, hash-bound A/B runtime catalog")
    parser.add_argument("--allow-live", action="store_true", help="Enable saved-credential jobs; same frozen 1/20 CNY budgets")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    parser.add_argument("--job-id")
    # Fault injection is host-only and disabled for live jobs. It is not an API input.
    parser.add_argument("--offline-stop-after-call", type=int, choices=range(1, 7), help=argparse.SUPPRESS)
    parser.add_argument("--offline-lease-seconds", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.allow_live and (args.offline_stop_after_call or args.offline_lease_seconds is not None):
        parser.error("Fault injection is offline-only")
    settings = settings_from_args(args)
    service = ResearchService(settings)
    from app.service.world_catalog import configure
    configure(service,args.world_catalog)
    if args.action == "serve":
        import uvicorn
        from app.service.api import create_app
        print(json.dumps({"url": f"http://127.0.0.1:{args.port}", "token_file": str(settings.directory / "auth.token"),
                          "live_enabled": settings.allow_live}, ensure_ascii=False), flush=True)
        uvicorn.run(create_app(service), host="127.0.0.1", port=args.port, access_log=False)
    elif args.action == "status":
        if not args.job_id: parser.error("--job-id required")
        row = service.store.get(args.job_id)
        print(json.dumps({key: row[key] for key in ("id", "state", "version", "generation", "reason", "result")}, ensure_ascii=False, indent=2))
    else:
        from app.service.runtime import Worker
        def stop(ordinal):
            if ordinal == args.offline_stop_after_call: os._exit(75)
        worker = Worker(service, lease_seconds=args.offline_lease_seconds or 90,
                        after_saved=stop if args.offline_stop_after_call else None)
        try:
            while True:
                row = worker.step()
                if row: print(json.dumps({"job_id": row["id"], "state": row["state"], "generation": row["generation"], "reason": row["reason"]}), flush=True)
                if args.once: break
                if row is None: time.sleep(0.5)
        except KeyboardInterrupt:
            # Lease expiry recovers completed calls, blocks ambiguous calls.
            return 130
    return 0


if __name__ == "__main__": raise SystemExit(main())
