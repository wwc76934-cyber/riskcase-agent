"""Independent SQL cross-check of training features; no labels or model calls."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import random
import sqlite3


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["status"] != "features_built" or manifest["labels_read"] is not False:
        raise ValueError("Completed label-free feature snapshot required")
    source = Path(manifest["cohort_report"]).parent / "dataset/facts.sqlite"
    target = args.manifest.parent / "features.sqlite"
    if sha256_file(source) != manifest["facts_sha256"] or sha256_file(target) != manifest["features_sha256"]:
        raise ValueError("Source/feature hash mismatch")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as facts, closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as features:
        features.row_factory = sqlite3.Row
        n_train = features.execute("SELECT COUNT(*) FROM features WHERE split='train'").fetchone()[0]
        upper = features.execute("SELECT MAX(rowid) FROM features").fetchone()[0]
        if not n_train or not upper:
            raise ValueError("Training feature rows required")
        rng = random.Random(42)
        sampled = {}
        for _ in range(10000):
            row = features.execute("SELECT * FROM features WHERE rowid=? AND split='train'", (rng.randint(1, upper),)).fetchone()
            if row is not None:
                sampled[row["transaction_id"]] = row
            if len(sampled) >= min(20, n_train):
                break
        if len(sampled) < min(20, n_train):
            raise ValueError("Could not obtain requested training check sample")
        for predicate in ("has_history_30d=0", "count_1h>=2"):
            row = features.execute(f"SELECT * FROM features WHERE split='train' AND {predicate} LIMIT 1").fetchone()
            if row is not None:
                sampled[row["transaction_id"]] = row
        checks = 0
        for identity, row in sampled.items():
            now = datetime.fromisoformat(row["event_time"])
            merchant = facts.execute("SELECT merchant_id FROM transactions WHERE transaction_id=?", (identity,)).fetchone()[0]
            for name, delta in (("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)),
                                ("7d", timedelta(days=7)), ("30d", timedelta(days=30))):
                count, amount, seen = facts.execute("""SELECT COUNT(*),COALESCE(SUM(amount_cents),0),
                    COALESCE(MAX(merchant_id=?),0) FROM transactions
                    WHERE user_id=? AND event_time>=? AND event_time<?""",
                    (merchant, row["user_id"], (now-delta).isoformat(), now.isoformat())).fetchone()
                expected = {f"count_{name}": count}
                if name in {"24h", "30d"}:
                    expected[f"sum_signed_cents_{name}"] = amount
                if name == "30d":
                    expected.update(merchant_seen_30d=seen, has_history_30d=int(count > 0))
                for field, value in expected.items():
                    if row[field] != value:
                        raise ValueError(f"Feature mismatch: {identity} {field}")
                    checks += 1
            previous = facts.execute("SELECT MAX(event_time) FROM transactions WHERE user_id=? AND event_time<?",
                                     (row["user_id"], row["event_time"])).fetchone()[0]
            expected_gap = None if previous is None else int((now - datetime.fromisoformat(previous)).total_seconds())
            if row["seconds_since_previous_in_snapshot"] != expected_gap:
                raise ValueError(f"Previous-event gap mismatch: {identity}")
            checks += 1
    print(json.dumps({"status": "passed", "scope": "training features only; no labels",
                      "sampled_rows": len(sampled), "independent_sql_checks": checks,
                      "feature_manifest_sha256": sha256_file(args.manifest),
                      "audit_script_sha256": sha256_file(Path(__file__)),
                      "limits": "Sampled fixed synthetic-source cross-check plus separate boundary tests; not exhaustive proof."}, indent=2))


if __name__ == "__main__":
    main()
