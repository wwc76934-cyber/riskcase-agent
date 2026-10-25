"""Self-authored public demo facts. No IBM records, labels, downloads or model claims."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "dataset").mkdir()
    source = directory / "dataset/facts.sqlite"
    cutoff = datetime(2018, 2, 10, 12)
    records, targets = [], {}
    for case in ("new-pattern", "ordinary-pattern", "missing-history"):
        short = case == "missing-history"
        for index in range(36 if not short else 3):
            when = cutoff - timedelta(days=(index % 23) + 1, hours=index % 3)
            if case == "new-pattern" and index < 5: when = cutoff - timedelta(minutes=5 * (index + 1))
            if short: when = cutoff - timedelta(minutes=10 * (index + 1))
            merchant = ("M-A", "M-B", "M-C")[index % 3]
            records.append((f"{case}-prior-{index}", len(records), case, "card-1", when.isoformat(),
                            100 + (index % 5) * 25, merchant, "Online" if index % 3 == 0 else "Chip", "5411", "validation"))
        identity = case + "-target"
        targets[case] = identity
        records.append((identity, len(records), case, "card-1", cutoff.isoformat(),
                        5000 if case == "new-pattern" else 125, "M-NEW" if case == "new-pattern" else "M-A", "Chip", "5411", "validation"))
        records.append((case + "-future", len(records), case, "card-1", (cutoff + timedelta(days=1)).isoformat(),
                        999999, "M-NEW", "Chip", "5411", "validation"))
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE transactions(transaction_id TEXT PRIMARY KEY,source_row INTEGER,user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)")
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", records)
    report = directory / "build-report.json"
    write_json(report, {"data_kind": "self_authored_fixture", "license": "CC0-1.0 (these authored example records only)",
                        "source_csv_fully_scanned": True, "coverage": {"warmup_start": "2018-01-01T00:00:00"},
                        "artifact_sha256": {"dataset/facts.sqlite": sha256_file(source)}, "records": len(records),
                        "labels": "None. Fixtures are not a fraud-detection performance dataset."})
    catalog = {}
    for case, identity in targets.items():
        path = export_case(directory / "cases" / case, source_database=source, expected_source_sha256=sha256_file(source),
                           transaction_id=identity, alert={"transaction_id": identity, "score": .9 if case == "new-pattern" else .3,
                               "data_kind": "self_authored_fixture", "selection": "handcrafted_not_trained_model"},
                           coverage_start="2018-02-10T00:00:00" if case == "missing-history" else "2018-01-01T00:00:00",
                           coverage_end="2018-03-01T00:00:00", full_source_scanned=case != "missing-history",
                           source_report_sha256=sha256_file(report))
        catalog[case] = str(path.resolve())
    write_json(directory / "catalog.json", catalog)
    files = {str(p.relative_to(directory)).replace("\\", "/"): sha256_file(p) for p in directory.rglob("*") if p.is_file()}
    write_json(directory / "bundle.json", {"version": 1, "data_kind": "self_authored_fixture", "artifact_sha256": files})
    return directory / "catalog.json"


def ensure(directory):
    directory = Path(directory)
    if not directory.exists(): return build(directory)
    bundle = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
    for name, expected in bundle["artifact_sha256"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256_file(path) != expected: raise ValueError("demo_fixture_changed")
    return directory / "catalog.json"
