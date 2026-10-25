"""Persist each completed stage; failed/cancelled runs never become completed."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from app.risk.history import sha256_file


def execute_module(root,module,arguments):
    command = [sys.executable,"-X","utf8","-B","-m",module,*map(str,arguments)]
    process = subprocess.Popen(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8")
    try:
        out,err = process.communicate(timeout=180)
    except BaseException:
        process.kill(); process.communicate()
        raise
    if process.returncode:
        raise ValueError(f"Stage {module} failed with exit code {process.returncode}: {err[-2000:]}")
    return json.loads(out)


class WorkflowRun:
    def __init__(self,directory: Path):
        directory.mkdir(parents=True,exist_ok=False)
        self.directory = directory
        self.state = {"status":"running","mode":"OFFLINE_SCRIPTED_AND_FAKE_NOT_REAL_LLM","real_LLM_requests":0,
            "human_approved":False,"deployed":False,"stages":[],"created_at":datetime.now(timezone.utc).isoformat()}
        self.save()

    def save(self):
        path = self.directory / "workflow.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        temporary.replace(path)

    def stage(self,name,action):
        if self.state["status"]!="running": raise ValueError("Workflow is no longer running")
        entry = {"name":name,"status":"running"}
        self.state["stages"].append(entry); self.save()
        try:
            result = action()
            entry.update(status="completed",result=result)
            self.save()
            return result
        except KeyboardInterrupt:
            entry["status"] = self.state["status"] = "cancelled"
            self.save()
            raise
        except Exception as exc:
            entry.update(status="failed",error_type=type(exc).__name__,error=str(exc)[:2000])
            self.state["status"]="failed"; self.save()
            raise

    def complete(self):
        if self.state["status"]!="running" or not self.state["stages"] or any(stage["status"]!="completed" for stage in self.state["stages"]):
            raise ValueError("All stages must succeed before completion")
        self.state["status"]="completed"; self.save()

    def bind(self,path):
        return {"path":str(path.resolve()),"sha256":sha256_file(path)}


def inspect_workflow(path):
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["status"]!="completed" or any(stage["status"]!="completed" for stage in state["stages"]):
        raise ValueError("Workflow is incomplete, failed or cancelled")
    if state["real_LLM_requests"]!=0 or state["human_approved"] or state["deployed"]:
        raise ValueError("Offline workflow provenance mismatch")
    documents = {}
    for name,artifact in state["artifacts"].items():
        target = Path(artifact["path"])
        if sha256_file(target)!=artifact["sha256"]: raise ValueError("Workflow artifact changed")
        documents[name] = json.loads(target.read_text(encoding="utf-8"))
    source = Path(documents["proposal"]["investigation_summary_path"])
    if source.resolve()!=Path(state["artifacts"]["investigation"]["path"]).resolve(): raise ValueError("Investigation link changed")
    if documents["proposal"]["investigation_summary_sha256"]!=state["artifacts"]["investigation"]["sha256"]:
        raise ValueError("Investigation digest link changed")
    replay_path = Path(documents["proposal"]["replay"]["output_directory"]) / "report.json"
    if replay_path.resolve()!=Path(state["artifacts"]["replay"]["path"]).resolve(): raise ValueError("Replay link changed")
    return {"status":"passed","workflow_sha256":sha256_file(path),"completed_stages":len(state["stages"]),
        "real_LLM_requests":0,"human_approved":False,"deployed":False,
        "limits":"Workflow status and linked summary hashes only; detailed source/SQL/review verifiers are separate."}
