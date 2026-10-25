"""Export audited stage receipts and examples; never regenerate paid slots."""

import json
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

from app.llm.cost_ledger import CostLedger
from app.risk.history import sha256_file
from app.risk.mixed_sql import SourceAudit
from app.risk.source_merchant_pack import write_json
from scripts.verify_natural import audit_stage
from scripts.verify_mixed import verify


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / "runs/natural-stage-v1"
    result = audit_stage(root, directory, code_root=directory / "frozen-code")
    result.update(date="2026-10-22", experiment_commit="1194e8b", status="completed_with_negative_model_value_result",
                  audit_sha256=sha256_file(directory / "audit.json"))
    demos = {}
    for name in ("natural-demo-offline-v1", "natural-demo-live-v1"):
        path = root / "runs" / name / "demo.json"
        demo = json.loads(path.read_text(encoding="utf-8"))
        if sha256_file(path.parent / "jobs.sqlite") != demo["jobs_sqlite_sha256"]: raise ValueError("Demo jobs changed")
        with SourceAudit(root / "data/cohorts/cohort-3bfdbaab486f/build-report.json") as source:
            for scenario in demo["scenarios"]:
                if "report" in scenario and verify(Path(scenario["report"]).parent / "summary.json", source) != scenario["sql_verification"]:
                    raise ValueError("Demo SQL proof differs")
        usage = [json.loads(p.read_text(encoding="utf-8")) for p in path.parent.glob("jobs/*/*/usage.json")]
        demos[name] = {"demo_sha256": sha256_file(path), "live": demo["live"], "transport": demo["transport"],
                       "scope_confirmation": demo["scope_confirmation"], "real_requests": sum(u["requests_sent"] for u in usage),
                       "cost_estimate_cny": str(sum((Decimal(u["ledger"]["run_committed_estimate_cny"]) for u in usage), Decimal(0))),
                       "scenarios": [{"question": r["question"], "states": r["states"], "sql_verified": r.get("sql_verification", {}).get("task_passed", False)} for r in demo["scenarios"]]}
    result["demos"] = demos
    tests = {}
    for name in ("full-tests.xml", "clean-tests.xml", "benchmark-tests.xml"):
        path = directory / name
        if path.exists():
            suite = next(ET.parse(path).getroot().iter("testsuite"))
            tests[name] = {**{k: int(suite.get(k, "0")) for k in ("tests", "failures", "errors", "skipped")},
                           "seconds": float(suite.get("time", "0")), "sha256": sha256_file(path)}
    result["test_evidence"] = tests
    ledger = CostLedger(root / "runs/llm-budget.sqlite").summary("stage-export-no-calls")
    result["project_committed_estimate_cny"] = ledger["project_committed_estimate_cny"]
    import sqlite3
    with sqlite3.connect(root / "runs/llm-budget.sqlite") as db:
        result["project_reservations"] = db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        result["project_unknown_cost_requests"] = db.execute("SELECT COUNT(*) FROM reservations WHERE estimated_cny IS NULL").fetchone()[0]
    # A wrong semantic scope can still have perfectly correct numbers: keep both artifacts.
    for task, dest in (("evaluation-02", "natural-scope-success.md"), ("evaluation-24", "natural-scope-overreach.md")):
        source_report = directory / "model" / task / "evidence/report.md"
        context = ("本例问题：这一笔比他近三十天所有正向交易都大吗。模型仅选择金额检查，与自建标准范围一致。\n\n" if task == "evaluation-02" else
                   "**这是失败反例，不能作为正确满足需求的报告。** 用户说“正金额占比先跳过，本次只比较笔数Top1”，模型却多选频次、单笔金额、新商户三项。下面数字全部经源SQL验证，但调查范围错了。评测只模拟确认，不能宣称真人同意了这个范围。\n\n")
        (root / "examples" / dest).write_text(context + source_report.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(root / "docs/natural-stage-v1-results.json", result)
    print(json.dumps({k: result[k] for k in ("stage", "status", "groups", "paired", "demos", "project_committed_estimate_cny", "project_reservations", "project_unknown_cost_requests")}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
