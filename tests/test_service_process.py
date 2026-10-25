"""Real process death/restart and localhost HTTP, with no external provider."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from app.service.runtime import ResearchService, Settings
from tests.test_merchant_research import manifest


def test_real_http_process_exit_and_recovery(tmp_path, manifest):
    repo = Path(__file__).resolve().parents[1]
    directory = tmp_path / "process-service"
    service = ResearchService(Settings(repo, directory, {"fixture": manifest}, "p"*40))
    (directory / "auth.token").write_text(service.settings.token, encoding="ascii")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"fixture": str(manifest)}), encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    base = [sys.executable, "-X", "utf8", "-B", "-m", "app.cli.service"]
    common = ["--directory", str(directory), "--catalog", str(catalog)]
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(base + ["serve", *common, "--port", str(port)], cwd=repo, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + service.settings.token}, timeout=5, trust_env=False) as client:
                until = time.monotonic() + 20
                while True:
                    try:
                        if client.get("/health").status_code == 200: break
                    except httpx.TransportError: pass
                    if process.poll() is not None or time.monotonic() > until: raise AssertionError("localhost service did not start")
                    time.sleep(.1)
                response = client.post("/jobs", json={"case_id": "fixture", "question": {"min_records": 1}}, headers={"Idempotency-Key": "process-request-0001"})
                assert response.status_code == 202
                identity = response.json()["id"]
                crashed = subprocess.run(base + ["worker", *common, "--once", "--offline-stop-after-call", "2", "--offline-lease-seconds", "1"], cwd=repo, capture_output=True, timeout=20)
                assert crashed.returncode == 75
                assert client.get(f"/jobs/{identity}").json()["state"] == "running"
                time.sleep(1.1)
                recovered = subprocess.run(base + ["worker", *common, "--once"], cwd=repo, capture_output=True, timeout=20)
                assert recovered.returncode == 0, recovered.stderr.decode("utf-8", errors="replace")
                result = client.get(f"/jobs/{identity}").json()
                assert result["state"] == "awaiting_review" and result["generation"] == 2
                assert result["result"]["replayed_client_responses"] == 2
                assert result["result"]["new_client_requests"] == 2
                assert client.get(f"/jobs/{identity}/artifacts/report.md").status_code == 200
                assert client.post("/jobs", json={"case_id": "fixture", "question": {"min_records": 1}}, headers={"Idempotency-Key": "process-request-0001"}).json()["id"] == identity
                cancelled = client.post("/jobs", json={"case_id": "fixture"}, headers={"Idempotency-Key": "process-cancel-0001"}).json()["id"]
                assert client.post(f"/jobs/{cancelled}/cancel").json()["state"] == "cancelled"
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
