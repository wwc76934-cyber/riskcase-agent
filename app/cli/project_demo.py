"""Fresh offline investigation -> fake proposal -> real local replay -> pending review."""

import json
import argparse
import subprocess
from pathlib import Path
from uuid import uuid4

from app.risk.review_package import review_package
from app.risk.review_store import ReviewStore
from app.risk.workflow_run import WorkflowRun, execute_module, inspect_workflow


def run_demo(root,directory):
    run = WorkflowRun(directory)
    def call(name,module,*arguments):
        return run.stage(name,lambda:execute_module(root,module,arguments))
    try:
        investigation = call("investigate","app.cli.investigate_case")
        # Existing CLI returns output_directory plus the run summary.
        case_dir = Path(investigation["output_directory"])
        case_summary = case_dir / "summary.json"
        if investigation["status"]!="completed_validated": raise ValueError("Investigation did not validate")
        call("verify_investigation","scripts.verify_case_run","--summary",case_summary)
        proposal = call("propose_and_replay","app.cli.propose_rule","--investigation-summary",case_summary)
        if proposal["status"]!="candidate_validated" or proposal["real_LLM_requests"]!=0:
            raise ValueError("Offline demo requires a validated fake proposal")
        proposal_summary = Path(proposal["output_directory"]) / "summary.json"
        replay_report = Path(proposal["replay"]["output_directory"]) / "report.json"
        call("verify_proposal","scripts.verify_rule_proposal","--summary",proposal_summary)
        call("verify_replay_sql","scripts.verify_candidate_replay","--report",replay_report)
        store = ReviewStore(directory / "demo-reviews.sqlite")
        package = review_package(proposal_summary)
        def register():
            state = store.register("demo-candidate",package,expected_version=0,actor="offline-demo-host",request_id="initial-demo-registration")
            return {"state":store.effective_status("demo-candidate",package),"audit":store.verify()}
        run.stage("register_pending_review",register)
        run.state["artifacts"] = {name:run.bind(path) for name,path in
            (("investigation",case_summary),("proposal",proposal_summary),("replay",replay_report))}
        run.state["code_commit"] = subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
        from app.risk.history import sha256_file
        run.state["code_file_sha256"] = {name:sha256_file(root / name) for name in
            ("app/cli/project_demo.py","app/risk/workflow_run.py","app/cli/propose_rule.py","scripts/verify_rule_proposal.py")}
        metrics = proposal["replay"]["metrics"]
        report = ("# RiskCase 离线演示\n\n状态：流程完成，候选待审阅。模型调用为 0。\n\n"
            "调查由离线策略决定，候选由 Fake 给出；工具与 SQL 回放读取实际本地合成数据。\n\n"
            f"新增审核 {metrics['added']['alerts']} 笔，其中欺诈标签 {metrics['added']['true_positives']} 笔、正常标签 {metrics['added']['false_positives']} 笔。\n\n"
            f"- [调查记录]({(case_dir / 'report.md').as_posix()})\n- [候选来源]({proposal_summary.as_posix()})\n- [回放报告]({replay_report.as_posix()})\n\n"
            "本演示独立使用 demo-reviews.sqlite，不修改真实候选审阅记录。没有批准、部署、最终测试或新模型能力结论。\n")
        (directory / "README.md").write_text(report,encoding="utf-8")
        run.complete()
    except KeyboardInterrupt:
        if run.state["status"]=="running": run.state["status"]="cancelled"; run.save()
    except Exception as exc:
        if run.state["status"]=="running":
            run.state.update(status="failed",error_type=type(exc).__name__,error=str(exc)[:2000]); run.save()
    return run


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect",type=Path,help="Check an existing workflow.json without rerunning stages")
    args = parser.parse_args()
    if args.inspect:
        print(json.dumps(inspect_workflow(args.inspect),ensure_ascii=False,indent=2))
        return 0
    directory = root / "runs" / f"demo-offline-v1-{uuid4().hex[:12]}"
    run = run_demo(root,directory)
    print(json.dumps({"output_directory":str(directory),**run.state},ensure_ascii=False,indent=2))
    return 0 if run.state["status"]=="completed" else 1


if __name__ == "__main__": raise SystemExit(main())
