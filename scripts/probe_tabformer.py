"""Read a bounded CSV prefix from IBM's archive; never extract or run its files.

This discovery probe measures accessibility and schema, not model quality or
population prevalence. Raw records are not written to the project or printed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import codecs
import csv
import json
from pathlib import Path
import tarfile
import time
from urllib.request import Request, urlopen


SOURCE_COMMIT = "ebb7cd68ee1897599568107740bc452104bbbaf8"
ARCHIVE_SHA256_DECLARED = "e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2"
URL = (
    "https://media.githubusercontent.com/media/IBM/TabFormer/"
    f"{SOURCE_COMMIT}/data/credit_card/transactions.tgz"
)


class BoundedReader:
    def __init__(self, response: object, byte_limit: int = 8 * 1024 * 1024):
        self.response = response
        self.byte_limit = byte_limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self.byte_limit - self.bytes_read
        if remaining <= 0:
            raise ValueError("Compressed download budget exhausted")
        chunk = self.response.read(min(size if size >= 0 else remaining, remaining))
        self.bytes_read += len(chunk)
        return chunk


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    args = parser.parse_args()
    if not 1 <= args.rows <= 100000:
        parser.error("--rows must be between 1 and 100000")

    start = time.monotonic()
    result = {
        "source_commit": SOURCE_COMMIT,
        "url": URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "data_kind": "synthetic",
        "sampling": "first CSV member, prefix only; not a random sample",
        "requested_rows": args.rows,
        "archive_sha256_declared_by_lfs": ARCHIVE_SHA256_DECLARED,
        "full_archive_hash_verified": False,
        "license_status": "repository Apache-2.0; data redistribution scope not yet cleared",
    }
    try:
        request = Request(URL, headers={"User-Agent": "RiskCase-data-discovery/0.1"})
        with urlopen(request, timeout=20) as response:
            stream = BoundedReader(response)
            with tarfile.open(fileobj=stream, mode="r|gz") as archive:
                for member in archive:
                    if not member.isfile() or not member.name.lower().endswith(".csv"):
                        continue
                    handle = archive.extractfile(member)
                    if handle is None:
                        raise ValueError("CSV member has no readable content")
                    with codecs.getreader("utf-8-sig")(handle) as text:
                        rows = csv.DictReader(text)
                        result["fields"] = rows.fieldnames
                        result["csv_member"] = member.name
                        labels: Counter[str] = Counter()
                        users: set[str] = set()
                        merchants: set[str] = set()
                        dates: list[str] = []
                        count = 0
                        for row in rows:
                            count += 1
                            labels[row.get("Is Fraud?", "<field absent>")] += 1
                            users.add(row.get("User", "<field absent>"))
                            merchants.add(row.get("Merchant Name", "<field absent>"))
                            if all(row.get(key) for key in ("Year", "Month", "Day")):
                                dates.append(
                                    f"{int(row['Year']):04}-{int(row['Month']):02}-{int(row['Day']):02}"
                                )
                            if count >= args.rows or time.monotonic() - start >= 45:
                                break
                        result.update(
                            status="sample_read",
                            sampled_rows=count,
                            label_counts=dict(labels),
                            distinct_users_in_prefix=len(users),
                            distinct_merchants_in_prefix=len(merchants),
                            date_min_in_prefix=min(dates) if dates else None,
                            date_max_in_prefix=max(dates) if dates else None,
                            compressed_bytes_read=stream.bytes_read,
                            limitations=[
                                "Prefix may be ordered by user; cannot estimate population metrics.",
                                "No label-availability time established; no point-in-time claim.",
                                "No full-data validation, training, or Agent evaluation performed.",
                            ],
                        )
                    break
                else:
                    raise ValueError("No CSV found inside bounded archive stream")
    except Exception as exc:
        result.update(status="probe_failed", error_type=type(exc).__name__, error=str(exc))
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    output = Path(__file__).resolve().parents[1] / "data" / "probes" / "tabformer-sample.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "sample_read":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
