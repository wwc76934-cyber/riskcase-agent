"""Published capacity evidence must stay bound to the independently checked report."""

import json
import sqlite3

import pytest

from app.risk.capacity_v2 import evaluate, materialize
from app.risk.source_merchant_pack import write_json
from scripts.export_capacity_v2 import comparison_table, verified_evidence
from scripts.verify_capacity_v2 import verify
from tests.test_capacity_v2 import frozen, source


@pytest.fixture
def checked(frozen):
    root, directory, _, _ = frozen
    materialize(root, directory)
    evaluate(root, directory)
    verify(root, directory)
    return frozen


def test_export_reads_bound_evidence_without_opening_labels(checked, monkeypatch):
    root, directory, _, _ = checked
    connect = sqlite3.connect
    def guarded(path, *args, **kwargs):
        if "evaluation-labels.sqlite" in str(path):
            raise AssertionError("Export must use the existing checked report, not query labels")
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    protocol, manifest, report, proof = verified_evidence(root, directory)
    assert protocol["baseline_kind"] == "neural_v2_ensemble"
    assert manifest["labels_read"] is False and proof["status"] == "passed"
    assert report["metrics"]["10/neural"]["true_positives"] == 2


def test_export_refuses_changed_report(checked):
    root, directory, _, _ = checked
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    report["metrics"]["10/neural"]["true_positives"] = 200
    write_json(directory / "report.json", report)
    with pytest.raises(ValueError, match="evidence_binding_differs"):
        verified_evidence(root, directory)


def test_export_refuses_registered_or_approved_research_package(checked):
    root, directory, _, _ = checked
    package = json.loads((directory / "research-package.json").read_text(encoding="utf-8"))
    package["status"] = "approved"
    write_json(directory / "research-package.json", package)
    with pytest.raises(ValueError, match="research_package_binding_differs"):
        verified_evidence(root, directory)


def test_table_reports_capacity_vacancies_and_incremental_loss(checked):
    root, directory, _, _ = checked
    _, _, report, _ = verified_evidence(root, directory)
    table = comparison_table(report)
    # Hand-authored fixture: 36 rows / 4 positives, two daily10 queues.
    # Pure neural captures 2, rule-first captures 4, rule-only leaves 8 seats.
    assert "| 10 | 纯深度学习 | 20 | 2 | 18 | 50.00% | 0 | 0 | +0 | 0 |" in table
    assert "| 10 | 规则优先 | 20 | 4 | 16 | 100.00% | 2 | 0 | +2 | 0 |" in table
    assert "| 10 | 纯规则 | 12 | 4 | 8 | 100.00% | 2 | 0 | +2 | 8 |" in table
