"""Preflight or run one case with a bounded SiliconFlow client; no stored API key."""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import httpx

from app.llm.cost_ledger import CostLedger
from app.llm.credentials import load_key as load_saved_key
from app.llm.offline_investigator import OfflineInvestigationClient
from app.llm.siliconflow_client import SiliconFlowClient, validate_policy, MODEL
from app.risk.history import sha256_file
from app.risk.investigation import run_investigation
from app.tools.case_tools import CaseTools


def local_key(prompt=False):
    if prompt:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError("Hidden credential input requires a local interactive terminal")
        return getpass.getpass("硅基流动 API Key（隐藏输入，仅本次进程使用，不写文件）: ").strip()
    return os.environ.get("SILICONFLOW_API_KEY", "").strip() or load_saved_key() or ""


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--mock", action="store_true", help="Exercise HTTP/usage handling, with no network or real charge")
    modes.add_argument("--live", action="store_true", help="Send case facts to SiliconFlow, under the frozen local budgets")
    parser.add_argument("--prompt-key", action="store_true", help="Hidden local terminal input; never save a key")
    parser.add_argument("--case-manifest", type=Path, default=root / "runs/case-v1-f4a58a923f32/case/manifest.json")
    args = parser.parse_args(argv)
    if args.prompt_key and not args.live:
        parser.error("--prompt-key requires --live")
    policy_path = root / "config/siliconflow-live-v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    validate_policy(policy)
    if args.preflight or not (args.live or args.mock):
        ready, reason = False, None
        try:
            tools = CaseTools(args.case_manifest)
            ready = "2018-01-01" <= tools.manifest["cutoff"] < "2019-01-01"
        except (OSError,ValueError,KeyError):
            reason = "case_snapshot_missing_or_invalid"
        credential_issue = None
        try:
            configured = bool(local_key())
        except ValueError:
            configured,credential_issue = False,"windows_credential_store_unavailable_in_this_process"
        print(json.dumps({"status":"preflight_only_no_network", "credential_present":configured,
            "case_ready":ready,"case_issue":reason,"credential_issue":credential_issue,"live_ready":configured and ready,
            "provider":policy["provider"],"endpoint":policy["endpoint"],"model":policy["model"],
            "maximum_requests":policy["max_requests"],"run_estimate_budget_cny":policy["run_budget_cny"],
            "project_estimate_budget_cny":policy["project_budget_cny"],"real_requests_sent":0,
            "next_local_command":"python -X utf8 -B -m app.cli.investigate_live --live"},ensure_ascii=False,indent=2))
        return 0
    try:
        key = "HTTP-MOCK-PLACEHOLDER" if args.mock else local_key(args.prompt_key)
        if not key:
            print("未配置密钥。请先运行 python -m app.cli.credentials --set 一次保存；不要把密钥贴到聊天。",file=sys.stderr)
            return 2
        source_tools = CaseTools(args.case_manifest)
        if not "2018-01-01" <= source_tools.manifest["cutoff"] < "2019-01-01":
            raise ValueError("Only development-period cases are permitted")
    except (OSError,ValueError,KeyError):
        print("本地密钥输入或 case 配置不可用；未发送请求。",file=sys.stderr)
        return 2
    run_id = f"{'http-mock' if args.mock else 'live'}-case-v1-{uuid4().hex[:12]}"
    directory = root / "runs" / run_id
    directory.mkdir(parents=True,exist_ok=False)
    (directory / "case").mkdir()
    for name in ("manifest.json","facts.sqlite"):
        shutil.copyfile(args.case_manifest.parent / name, directory / "case" / name)
    tools = CaseTools(directory / "case/manifest.json")
    (directory / "policy.json").write_text(json.dumps(policy,indent=2)+"\n",encoding="utf-8")
    mode = "siliconflow_HTTP_MOCK_NOT_REAL_LLM" if args.mock else "siliconflow_live"
    ledger = CostLedger(directory / "mock-budget.sqlite" if args.mock else root / "runs/llm-budget.sqlite")
    http = None
    if args.mock:
        scripted = OfflineInvestigationClient()
        counter = [0]
        def handler(request):
            counter[0] += 1
            payload = json.loads(request.content)
            message = scripted.chat(payload["messages"],payload["tools"])
            message["reasoning_content"] = "MOCK_INTERNAL_NOT_SAVED"
            return httpx.Response(200,json={"id":f"http-mock-{counter[0]}","model":MODEL,
                "choices":[{"message":message,"finish_reason":"tool_calls" if message.get("tool_calls") else "stop"}],
                "usage":{"prompt_tokens":2048,"completion_tokens":128,"total_tokens":2176,
                    "completion_tokens_details":{"reasoning_tokens":0}}})
        http = httpx.Client(transport=httpx.MockTransport(handler))
    client = SiliconFlowClient(key,policy=policy,ledger=ledger,run_id=run_id,http_client=http)
    try:
        result = run_investigation(client,tools,client_mode=mode,max_iterations=policy["max_requests"],allow_format_repair=True)
    except KeyboardInterrupt:
        result = {"status":"cancelled","client_mode":mode,"client_requests":client.sent_requests,
            "tool_attempts":tools.attempts,"findings":None,"report_markdown":None,
            "error":"Interrupted; provider charge may be uncertain","evidence":tools.evidence,"tool_trace":tools.trace,
            "messages":[]}
    except Exception:
        result = {"status":"client_failure","client_mode":mode,"client_requests":client.sent_requests,
            "tool_attempts":tools.attempts,"findings":None,"report_markdown":None,
            "error":"Client failed; inspect sanitized usage audit","evidence":tools.evidence,"tool_trace":tools.trace,
            "messages":[]}
    finally:
        client.close()
        if http is not None:
            http.close()
    audit = client.snapshot()
    audit["simulation_not_real_usage"] = args.mock
    result = client.scrub(result)
    if result.get("report_markdown") is not None:
        (directory / "report.md").write_text(result.pop("report_markdown"),encoding="utf-8")
    (directory / "trace.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (directory / "usage.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    names = ["case/manifest.json","case/facts.sqlite","policy.json","trace.json","usage.json"]
    if (directory / "report.md").exists(): names.append("report.md")
    code_paths = ["app/cli/investigate_live.py","app/llm/siliconflow_client.py","app/llm/cost_ledger.py",
        "app/risk/investigation.py","app/tools/case_tools.py"]
    summary = {"status":result["status"],"client_mode":mode,"data_kind":"synthetic",
        "created_at":datetime.now(timezone.utc).isoformat(),
        "transaction_id":tools.manifest["target_transaction_id"],"client_requests":result["client_requests"],
        "tool_attempts":tools.attempts,"real_LLM_requests":0 if args.mock else client.sent_requests,
        "credential_source":"mock_placeholder" if args.mock else "interactive_memory_only" if args.prompt_key else "process_environment_or_windows_store",
        "findings":result["findings"],"usage_estimate_is_simulated":args.mock,
        "estimated_committed_cny":audit["ledger"]["run_committed_estimate_cny"],
        "unknown_cost_requests":audit["ledger"]["unknown_cost_requests"],"last_error_code":audit["last_error_code"],
        "code_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
        "code_file_sha256":{name:sha256_file(root / name) for name in code_paths},
        "artifact_sha256":{name:sha256_file(directory / name) for name in names},
        "limits":["HTTP Mock results are not actual model ability or actual fees.",
            "Local estimated budgets are not provider-enforced caps; provider invoice is authoritative.",
            "One case smoke test is not independent task evaluation."]}
    (directory / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output_directory":str(directory),**summary},ensure_ascii=False,indent=2))
    return 0 if result["status"] == "completed_validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
