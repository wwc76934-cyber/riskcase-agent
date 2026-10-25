"""Read-only training quality and split coverage; never read any labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report.get("status") != "research_snapshot_built" or not report.get("source_csv_fully_scanned"):
        raise ValueError("Completed snapshot report required")
    database = args.report.parent / "dataset/facts.sqlite"
    if sha256_file(database) != report["artifact_sha256"]["dataset/facts.sqlite"]:
        raise ValueError("Facts hash mismatch")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        queries = {
            "training_merchants_with_multiple_mcc": """SELECT COUNT(*) FROM (
                SELECT merchant_id FROM transactions NOT INDEXED WHERE split='train'
                GROUP BY merchant_id HAVING COUNT(DISTINCT mcc)>1)""",
            "training_merchants_with_multiple_observed_locations": """SELECT COUNT(*) FROM (
                SELECT merchant_id FROM (SELECT DISTINCT merchant_id,merchant_city,merchant_state,zip
                FROM transactions NOT INDEXED WHERE split='train') GROUP BY merchant_id HAVING COUNT(*)>1)""",
            "training_missing_mcc": "SELECT COUNT(*) FROM transactions NOT INDEXED WHERE split='train' AND mcc=''",
            "training_missing_channel": "SELECT COUNT(*) FROM transactions NOT INDEXED WHERE split='train' AND channel=''",
        }
        result = {name: connection.execute(query).fetchone()[0] for name, query in queries.items()}
        result["training_channels"] = dict(connection.execute(
            "SELECT channel,COUNT(*) FROM transactions NOT INDEXED WHERE split='train' GROUP BY channel"))
        coverage = connection.execute("""WITH coverage AS (
            SELECT user_id, MAX(split='train') seen_train, MAX(split='validation') seen_validation,
            MAX(split='test') seen_test, MAX(split!='test') seen_pretest
            FROM transactions NOT INDEXED GROUP BY user_id)
            SELECT SUM(seen_train),SUM(seen_validation),SUM(seen_test),
            SUM(seen_test AND NOT seen_train),SUM(seen_validation AND NOT seen_train),
            SUM(seen_test AND NOT seen_pretest) FROM coverage""").fetchone()
        result["user_coverage"] = dict(zip(("train_users", "validation_users", "test_users",
            "test_users_not_observed_in_train", "validation_users_not_observed_in_train",
            "test_users_not_observed_before_test_within_snapshot"), coverage))
    print(json.dumps({"report_sha256": sha256_file(args.report), "audit_script_sha256": sha256_file(Path(__file__)),
                      "scope": "train field checks and split user coverage; no labels",
                      **result, "interpretation": "Multiple locations may mean chain-level identifiers; not automatically invalid."}, indent=2))


if __name__ == "__main__":
    main()
