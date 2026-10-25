import json
import sqlite3

import pytest

from app.risk.case_snapshot import export_case
from app.risk.history import sha256_file
from app.risk.merchant_stats import merchant_distribution
from app.risk.merchant_research import (
    MerchantQuestion, MerchantResearchTools, ScriptedMerchantClient, compare_distributions,
    run_merchant_research, validate_merchant_answer,
)
from app.tools.case_tools import CaseTools


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    root = tmp_path_factory.mktemp("merchant")
    (root / "dataset").mkdir()
    source = root / "dataset/facts.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE transactions(transaction_id TEXT,source_row INTEGER,user_id TEXT,card_id TEXT,event_time TEXT,amount_cents INTEGER,merchant_id TEXT,channel TEXT,mcc TEXT,split TEXT)")
        rows = [("A",100),("A",100),("B",50),("B",-900),("B",0)]
        records = [(str(i),i,"u","c","2018-01-30T12:00:00",amount,merchant,"online","1","validation") for i,(merchant,amount) in enumerate(rows)]
        records += [(str(i),i,"u","c","2018-01-20T12:00:00",100,"B","online","1","validation") for i in range(5,9)]
        records += [("target",9,"u","c","2018-02-01T12:00:00",9999,"Z","online","1","validation"),
            ("same-time",10,"u","c","2018-02-01T12:00:00",999999,"X","online","1","validation"),
            ("future",11,"u","c","2018-02-02T12:00:00",999999,"X","online","1","validation"),
            ("other-user",12,"other","c","2018-01-30T12:00:00",999999,"X","online","1","validation")]
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",records)
    report = root / "build-report.json"
    report.write_text(json.dumps({"artifact_sha256":{"dataset/facts.sqlite":sha256_file(source)}}),encoding="utf-8")
    return export_case(root / "case",source_database=source,expected_source_sha256=sha256_file(source),
        transaction_id="target",alert={"transaction_id":"target","score":0.8},coverage_start="2017-12-01T00:00:00",
        coverage_end="2019-01-01T00:00:00",full_source_scanned=True,source_report_sha256=sha256_file(report))


def observations(manifest):
    tools = CaseTools(manifest,allow_merchant_distribution=True)
    tools.dispatch("get_alert_transaction","{}")
    short = tools.dispatch("summarize_merchant_distribution",'{"lookback_hours":168}')["data"]
    long = tools.dispatch("summarize_merchant_distribution",'{"lookback_hours":720}')["data"]
    return tools,short,long


def test_exact_denominators_and_scoped_history(manifest):
    _,short,long = observations(manifest)
    assert (short["record_count"],long["record_count"]) == (5,9)
    assert short["top_by_count"][0] == {"merchant_id":"B","record_count":3}
    assert short["top1_positive_amount_cents"] == 200
    assert short["positive_amount_denominator_cents"] == 250
    assert short["negative_amount_sum_cents"] == -900
    assert short["zero_record_count"] == 1
    assert compare_distributions(short,long,MerchantQuestion(min_records=1)) == {"count_top1":"decreased","positive_amount_top1":"increased"}


def test_default_v1_tool_contract_unchanged(manifest):
    tools = CaseTools(manifest)
    assert len(tools.schemas) == 3
    assert not tools.dispatch("summarize_merchant_distribution",'{"lookback_hours":168}')["ok"]


@pytest.mark.parametrize("args",['{"lookback_hours":1}','{"lookback_hours":true}','{"lookback_hours":168,"user_id":"other"}','{"lookback_hours":168,"lookback_hours":720}'])
def test_distribution_rejects_unsupported_queries(manifest,args):
    tools = CaseTools(manifest,allow_merchant_distribution=True,max_calls=1)
    assert not tools.dispatch("summarize_merchant_distribution",args)["ok"]
    assert tools.dispatch("get_alert_transaction","{}")["error_type"] == "ToolBudgetExceeded"


def test_minimum_and_coverage_are_required(manifest):
    _,short,long = observations(manifest)
    assert set(compare_distributions(short,long,MerchantQuestion()).values()) == {"insufficient_evidence"}
    short["complete_within_source"] = False
    assert set(compare_distributions(short,long,MerchantQuestion(min_records=1)).values()) == {"insufficient_evidence"}


def test_no_positive_amount_never_divides_by_zero():
    row = {**merchant_distribution([{"merchant_id":"A","amount_cents":-1}]),"complete_within_source":True}
    result = compare_distributions(row,row,MerchantQuestion(min_records=1))
    assert result == {"count_top1":"unchanged","positive_amount_top1":"insufficient_evidence"}


def test_empty_and_stable_ties_and_full_aggregation():
    assert merchant_distribution([])["count_denominator"] == 0
    result = merchant_distribution([{"merchant_id":m,"amount_cents":1} for m in ["D","C","B","A"]])
    assert result["merchant_count"] == 4 and result["count_denominator"] == 4
    assert result["merchant_preview_truncated"]
    assert [row["merchant_id"] for row in result["top_by_count"]] == ["A","B","C"]


@pytest.mark.parametrize("row",[{"merchant_id":"","amount_cents":1},{"merchant_id":"A","amount_cents":True},{"merchant_id":1,"amount_cents":10}])
def test_invalid_raw_values(row):
    with pytest.raises(ValueError): merchant_distribution([row])


@pytest.mark.parametrize("kwargs",[{"short_hours":720},{"short_hours":True},{"min_records":0},{"long_hours":48}])
def test_question_contract(kwargs):
    with pytest.raises(ValueError): MerchantQuestion(**kwargs)


def test_scripted_end_to_end_and_report(manifest):
    question = MerchantQuestion(min_records=1)
    tools = MerchantResearchTools(manifest)
    result = run_merchant_research(ScriptedMerchantClient(question),tools,question,client_mode="SCRIPTED_NOT_LLM")
    assert result["status"] == "completed_validated"
    assert result["tool_attempts"] == 3 and result["client_requests"] == 4
    assert result["findings"]["positive_amount_top1"] == "increased"
    assert "3/5 = 60.00%" in result["report_markdown"]
    assert "两个独立时期" in result["report_markdown"]


def test_task_permissions_enforced_beyond_schema(manifest):
    tools = MerchantResearchTools(manifest)
    assert len(tools.schemas)==2
    assert not tools.dispatch("summarize_history",'{"lookback_hours":168}')["ok"]


@pytest.mark.parametrize("tamper",[None,"aggregation","projection","report"])
def test_independent_source_verification(manifest,tmp_path,tamper):
    import shutil
    from dataclasses import asdict
    from scripts.verify_merchant_research import verify
    question = MerchantQuestion(min_records=1)
    result = run_merchant_research(ScriptedMerchantClient(question),MerchantResearchTools(manifest),question,client_mode="SCRIPTED_NOT_LLM")
    shutil.copytree(manifest.parent,tmp_path / "case")
    report = result.pop("report_markdown")
    if tamper=="aggregation":
        entry = result["tool_trace"][1]["result"]["data"]
        old = entry["evidence_id"]
        entry["count_denominator"]+=1
        import hashlib
        content = {k:v for k,v in entry.items() if k!="evidence_id"}
        new = "ev-"+hashlib.sha256(json.dumps(content,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:24]
        entry["evidence_id"]=new
        result["evidence"].pop(old)
        result["evidence"][new]=entry
        result["findings"]["evidence_ids"][0]=new
    if tamper=="projection":
        with sqlite3.connect(tmp_path / "case/facts.sqlite") as db: db.execute("UPDATE transactions SET amount_cents=1")
        m = json.loads((tmp_path / "case/manifest.json").read_text(encoding="utf-8"))
        m["projection_sha256"]=sha256_file(tmp_path / "case/facts.sqlite")
        (tmp_path / "case/manifest.json").write_text(json.dumps(m),encoding="utf-8")
    (tmp_path / "trace.json").write_text(json.dumps(result),encoding="utf-8")
    (tmp_path / "question.json").write_text(json.dumps(asdict(question)),encoding="utf-8")
    (tmp_path / "policy.json").write_text("{}",encoding="utf-8")
    (tmp_path / "report.md").write_text(report+ ("incorrect" if tamper=="report" else ""),encoding="utf-8")
    summary = {key:result[key] for key in ("status","findings","tool_attempts","client_requests","first_attempt","format_repair","failure_stage","maximum_client_requests")}
    summary.update(question=asdict(question),artifact_sha256={str(p.relative_to(tmp_path)).replace("\\","/"):sha256_file(p) for p in tmp_path.rglob("*") if p.is_file()})
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary),encoding="utf-8")
    if tamper:
        with pytest.raises(ValueError): verify(summary_path,manifest.parent.parent / "build-report.json")
    else:
        assert verify(summary_path,manifest.parent.parent / "build-report.json")["observations_checked"]==3


@pytest.mark.parametrize("mutation",["wrong_comparison","wrong_reference","extra_verdict","duplicate_reference","wrong_window"])
def test_reject_unsupported_model_claims(manifest,mutation):
    tools,short,long = observations(manifest)
    question = MerchantQuestion(min_records=1)
    body = {**compare_distributions(short,long,question),"evidence_ids":[short["evidence_id"],long["evidence_id"]]}
    if mutation=="wrong_comparison": body["count_top1"]="increased"
    if mutation=="wrong_reference": body["evidence_ids"][0]="invented"
    if mutation=="extra_verdict": body["fraud"]=True
    if mutation=="duplicate_reference": body["evidence_ids"][0]=body["evidence_ids"][1]
    if mutation=="wrong_window": question=MerchantQuestion(short_hours=24,min_records=1)
    with pytest.raises(ValueError): validate_merchant_answer(json.dumps(body),tools.evidence,question)
