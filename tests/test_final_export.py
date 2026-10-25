"""A parent audit receipt cannot certify a changed or escaping child artifact."""

import pytest

from scripts.export_final_results import bound, check_nested_artifacts, check_predictions, checked_tests, sha


def test_audit_receipt_does_not_mask_changed_evidence(tmp_path):
    child = tmp_path / "route.json"
    child.write_text('{"checks":["frequency"]}', encoding="utf-8")
    receipt = {"artifact_sha256": {"route.json": sha(child)}}
    check_nested_artifacts(tmp_path, receipt)
    # Parent receipt has not changed; the actual material did.
    child.write_text('{"checks":["amount"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="binding differs"):
        check_nested_artifacts(tmp_path, receipt)


def test_prediction_manifest_binds_underlying_database_bytes(tmp_path):
    database = tmp_path / "predictions.sqlite"
    database.write_bytes(b"frozen-predictions-fixture")
    manifest = {"predictions_sha256": sha(database)}
    check_predictions(tmp_path, manifest)
    database.write_bytes(b"changed-predictions-fixture")
    with pytest.raises(ValueError, match="binding differs"):
        check_predictions(tmp_path, manifest)


def test_correct_digest_does_not_allow_escape_from_artifact_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("test fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="binding differs"):
        bound(root, "../outside.txt", sha(outside))


@pytest.mark.parametrize("body", ["<testsuite/>", '<testsuite><testcase><failure/></testcase></testsuite>',
                                  '<testsuite><testcase><error/></testcase></testsuite>',
                                  '<testsuite><testcase><skipped/></testcase></testsuite>'])
def test_empty_or_nonpassing_test_receipt_cannot_close_delivery(tmp_path, body):
    path = tmp_path / "tests.xml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="passing test receipt"):
        checked_tests(path)


def test_valid_test_receipt_counts_actual_cases(tmp_path):
    path = tmp_path / "tests.xml"
    path.write_text('<testsuites><testsuite tests="999" time="1.2"><testcase name="actual"/></testsuite></testsuites>', encoding="utf-8")
    assert checked_tests(path)["passed"] == 1
