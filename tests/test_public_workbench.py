"""Fresh checkout demo, UI auth, data/source identity and real read-only reports."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.risk.demo_fixture import build, ensure
from app.risk.mixed_sql import SourceAudit
from app.service.api import create_app
from app.service.runtime import ResearchService, Settings, Worker
from scripts.verify_mixed import verify


@pytest.fixture
def fixture_service(tmp_path):
    catalog = ensure(tmp_path / "public-data")
    settings = Settings(Path(__file__).resolve().parents[1], tmp_path / "service", json.loads(catalog.read_text(encoding="utf-8")), "t"*40)
    return ResearchService(settings), catalog.parent


def test_fixture_idempotent_and_no_overwrite(tmp_path):
    directory = tmp_path / "demo"
    path = ensure(directory)
    assert ensure(directory) == path
    with pytest.raises(FileExistsError): build(directory)
    (directory / "cases/new-pattern/facts.sqlite").write_bytes(b"changed")
    with pytest.raises(ValueError, match="fixture_changed"): ensure(directory)


def test_public_page_contains_no_credentials_and_apis_require_auth(fixture_service):
    service, _ = fixture_service
    with TestClient(create_app(service)) as api:
        page = api.get("/")
        assert page.status_code == 200 and "支付预警调查与审阅" in page.text
        assert service.settings.token not in page.text and "api_key=" not in page.text.lower()
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert page.headers["Cache-Control"] == "no-store"
        for path in ("/cases/details", "/jobs", "/health"):
            assert api.get(path).status_code == 401
        assert api.get("/", headers={"Host": "evil.invalid"}).status_code == 400


def test_fresh_demo_full_reports_independently_sql_checked(fixture_service):
    service, directory = fixture_service
    with TestClient(create_app(service), headers={"Authorization": "Bearer " + service.settings.token}) as api, SourceAudit(directory / "build-report.json") as audit:
        assert len(api.get("/cases/details").json()["cases"]) == 3
        for index, case in enumerate(service.settings.catalog):
            response = api.post("/jobs", json={"case_id": case, "task_type": "mixed", "question": {"checks": ["frequency", "amount", "merchant", "count_share", "amount_share"]}}, headers={"Idempotency-Key": "fixture-"+str(index)})
            assert response.status_code == 202
            row = Worker(service).step()
            assert row["state"] == "awaiting_review"
            proof = verify(service.artifact(row["id"], "summary.json"), audit)
            assert proof["task_passed"] and not proof["labels_opened"]
            report = api.get(f"/jobs/{row['id']}/artifacts/report.md").text
            assert "自建教学案例" in report and "IBM合成数据" not in report
        listing = api.get("/jobs").json()["jobs"]
        assert len(listing) == 3 and all("spec" not in row and "result" not in row for row in listing)
        assert api.get("/jobs?limit=0").status_code == 422
