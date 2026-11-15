"""Portable evidence must fail closed on omissions and inconsistent accounting."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_check", ROOT / "scripts/verify_fraud_live_evidence.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class EvidenceCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pack = Path(self.temp.name) / "pack"
        shutil.copytree(ROOT / "docs/evidence/fraud-live-v4", self.pack)

    def rewrite_audit_and_manifest(self, mutate):
        path = self.pack / "audited-summary.json"
        audit = json.loads(path.read_text(encoding="utf-8"))
        mutate(audit)
        path.write_text(json.dumps(audit), encoding="utf-8")
        manifest_path = self.pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_both_committed_packs(self):
        rows = [CHECK.verify_pack(ROOT / "docs/evidence" / name)
                for name in ("fraud-live-v3", "fraud-live-v4")]
        self.assertEqual(sum(row["artifacts"] for row in rows), 11)
        self.assertEqual(sum(row["requests"] for row in rows), 60)

    def test_altered_payload_rejected(self):
        with (self.pack / "browser.json").open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            CHECK.verify_pack(self.pack)

    def test_missing_payload_rejected(self):
        (self.pack / "browser.json").unlink()
        with self.assertRaises(OSError):
            CHECK.verify_pack(self.pack)

    def test_rehashed_incorrect_total_rejected(self):
        self.rewrite_audit_and_manifest(lambda a: a.update(total_requests=1))
        with self.assertRaisesRegex(ValueError, "request total mismatch"):
            CHECK.verify_pack(self.pack)

    def test_rehashed_duplicate_run_rejected(self):
        self.rewrite_audit_and_manifest(lambda a: a["runs"].append(a["runs"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            CHECK.verify_pack(self.pack)

    def test_rehashed_human_claim_rejected(self):
        self.rewrite_audit_and_manifest(lambda a: a.update(independent_human_validation=True))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            CHECK.verify_pack(self.pack)

    def test_manifest_cannot_escape_pack(self):
        path = self.pack / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["artifact_sha256"]["../outside.json"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non-local filename"):
            CHECK.verify_pack(self.pack)


if __name__ == "__main__":
    unittest.main()
