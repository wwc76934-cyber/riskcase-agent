"""G0 discovery: validate rows, sample multiple users, export separate views."""

from __future__ import annotations

import argparse
from collections import Counter
import codecs
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tarfile
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from app.risk.data_contract import REQUIRED_COLUMNS, parse_tabformer_row, visible_agent_records
from app.risk.sampling import UserReservoirSampler
# Reuse the preceding discovery probe's fixed source and bounded network reader.
# This CLI is a diagnostic, not the production data adapter.
from scripts.probe_tabformer import ARCHIVE_SHA256_DECLARED, BoundedReader, SOURCE_COMMIT, URL


def write_jsonl(path: Path, records: list[dict]) -> str:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records)
    # Hash the actual bytes, avoiding Windows newline translation.
    payload = content.encode("utf-8")
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-rows", type=int, default=200000)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--per-user", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--as-of", default="2019-12-31T23:59:59")
    args = parser.parse_args()
    try:
        if not 1 <= args.scan_rows <= 500000:
            raise ValueError("scan-rows must be in 1..500000")
        cutoff = datetime.fromisoformat(args.as_of)
        if cutoff.tzinfo is not None:
            raise ValueError("as-of must use source-local time without a timezone suffix")
        sampler = UserReservoirSampler(
            max_users=args.users, rows_per_user=args.per_user, seed=args.seed
        )
    except ValueError as exc:
        parser.error(str(exc))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output = Path(__file__).resolve().parents[2] / "data" / "probes" / f"contract-{run_id}"
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run_id,
        "source_commit": SOURCE_COMMIT,
        "source_url": URL,
        "declared_archive_sha256": ARCHIVE_SHA256_DECLARED,
        "full_archive_hash_verified": False,
        "data_kind": "synthetic",
        "sampling": "first observed users; per-user reservoir over bounded scan",
        "configuration": vars(args),
        "label_availability": "unknown; no label confirmation time invented",
        "data_gate": "PARTIAL; not approved for model/generalization claims",
    }
    start = time.monotonic()
    scanned = 0
    accepted = 0
    rejected = Counter()
    try:
        request = Request(URL, headers={"User-Agent": "RiskCase-data-contract/0.1"})
        with urlopen(request, timeout=20) as response:
            stream = BoundedReader(response, byte_limit=16 * 1024 * 1024)
            with tarfile.open(fileobj=stream, mode="r|gz") as archive:
                for member in archive:
                    if not member.isfile() or not member.name.lower().endswith(".csv"):
                        continue
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise ValueError("CSV member unreadable")
                    manifest["csv_member"] = member.name
                    with codecs.getreader("utf-8-sig")(handle) as text:
                        reader = csv.DictReader(text)
                        if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
                            raise ValueError("CSV schema does not satisfy the data contract")
                        for source_row, row in enumerate(reader, start=1):
                            scanned += 1
                            try:
                                pair = parse_tabformer_row(
                                    row,
                                    source_id=f"tabformer:{ARCHIVE_SHA256_DECLARED}",
                                    source_row=source_row,
                                )
                            except ValueError as exc:
                                rejected[str(exc)] += 1
                            else:
                                sampler.add(*pair)
                                accepted += 1
                            if scanned >= args.scan_rows or time.monotonic() - start >= 45:
                                break
                    break
                else:
                    raise ValueError("CSV not found within compressed read budget")
            manifest["compressed_bytes_read"] = stream.bytes_read

        sample = sampler.sample()
        transactions = [tx for tx, _ in sample]
        agent_records = visible_agent_records(transactions, as_of=cutoff)
        labels = [
            {
                "transaction_id": label.transaction_id,
                "is_fraud": label.is_fraud,
                "available_at": None,
            }
            for _, label in sample
        ]
        agent_hash = write_jsonl(output / "agent-view.jsonl", agent_records)
        label_hash = write_jsonl(output / "evaluation-labels.jsonl", labels)
        manifest.update(
            status="discovery_sample_exported",
            scanned_rows=scanned,
            accepted_rows=accepted,
            rejected_rows=sum(rejected.values()),
            rejection_reasons=dict(rejected),
            distinct_users_in_scan=len(sampler.seen_counts),
            distinct_users_in_sample=len({tx.user_id for tx in transactions}),
            sampled_rows=len(sample),
            sample_rows_per_user=dict(Counter(tx.user_id for tx in transactions)),
            sample_date_min=min((tx.event_time.isoformat() for tx in transactions), default=None),
            sample_date_max=max((tx.event_time.isoformat() for tx in transactions), default=None),
            agent_visible_rows=len(agent_records),
            future_sample_rows_excluded=len(sample) - len(agent_records),
            # Aggregate labels are diagnostic output, never input to the Investigator.
            evaluation_only_label_counts=dict(Counter(str(label.is_fraud) for _, label in sample)),
            artifact_sha256={"agent-view.jsonl": agent_hash, "evaluation-labels.jsonl": label_hash},
            warnings=[
                "User selection and scan-prefix bias remain; no population prevalence estimate.",
                "Per-user reservoirs cannot serve as complete histories for rolling features.",
                "Separate files are not a permission sandbox; production tools still need isolation.",
                "Event-time filtering does not prove ingestion/label availability.",
            ],
        )
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    manifest["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output_directory": str(output), **manifest}, ensure_ascii=False, indent=2))
    if manifest["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
