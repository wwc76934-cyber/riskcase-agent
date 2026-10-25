"""Explicit capacity experiment stages; research registration never approves a rule."""

import argparse
import json
from pathlib import Path

from app.risk.capacity_experiment import freeze,materialize,evaluate,load_protocol
from app.risk.history import sha256_file
from app.risk.review_package import review_package
from app.risk.review_store import ReviewStore,digest
from app.risk.source_merchant_pack import write_json
from scripts.verify_capacity_experiment import verify


def capacity_package(root,directory):
    protocol=load_protocol(root,directory)
    proof=json.loads((directory / "verification.json").read_text())
    if proof["status"]!="passed" or proof["report_sha256"]!=sha256_file(directory / "report.json") or proof["protocol_sha256"]!=sha256_file(directory / "protocol.json") or proof["script_sha256"]!=sha256_file(root / "scripts/verify_capacity_experiment.py"):
        raise ValueError("Current independent verification required")
    report=json.loads((directory / "report.json").read_text(encoding="utf-8"))
    plan_path=directory / "plan-manifest.json"
    plan=json.loads(plan_path.read_text())
    if proof["plan_manifest_sha256"]!=sha256_file(plan_path) or report["plan_manifest_sha256"]!=sha256_file(plan_path) or plan["protocol_sha256"]!=proof["protocol_sha256"]:
        raise ValueError("Plan material changed after verification")
    if sha256_file(directory / "ranking-inputs.sqlite")!=plan["population_sha256"] or sha256_file(directory / "planned-reviews.sqlite")!=plan["plans_sha256"] or sha256_file(Path(protocol["labels_path"]))!=protocol["labels_sha256"]:
        raise ValueError("Frozen data or selection changed after verification")
    base=review_package(Path(protocol["proposal_path"]))
    if base!=protocol["proposal_package"] or report["human_approved"] or report["deployed"]: raise ValueError("Original proposal binding changed")
    return {**base,"capacity_experiment":{"protocol_sha256":proof["protocol_sha256"],"report_sha256":proof["report_sha256"],
        "plan_manifest_sha256":sha256_file(directory / "plan-manifest.json"),"verification_sha256":sha256_file(directory / "verification.json"),
        "primary_budget":protocol["primary_budget"],"primary_metrics":{p:report["metrics"][f"10/{p}"] for p in protocol["policies"]},"scope":"Previously used synthetic development data; human review and independent validation remain required."}}


def register(root,directory):
    protocol=load_protocol(root,directory);package=capacity_package(root,directory)
    store=ReviewStore(root / "runs/reviews.sqlite")
    candidate=protocol["original_review_candidate_id"]
    current=store.current(candidate)
    if current["package"]["proposal_sha256"]!=package["proposal_sha256"]: raise ValueError("Review belongs to another proposal")
    if current["package_hash"]!=digest(package):
        store.register(candidate,package,expected_version=current["version"],actor="capacity-experiment-host",request_id="capacity-"+sha256_file(directory / "protocol.json")[:24])
    result={"status":store.effective_status(candidate,package),"audit":store.verify(),"capacity_report_sha256":sha256_file(directory / "report.json")}
    write_json(directory / "review-link.json",result)
    return result


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("freeze","plan","evaluate","verify","register","all"))
    parser.add_argument("--directory",type=Path,required=True)
    args=parser.parse_args();directory=args.directory.resolve()
    actions={"freeze":freeze,"plan":materialize,"evaluate":evaluate,"verify":verify,"register":register}
    if args.action=="all":
        result={}
        for name,action in actions.items():
            result[name]=action(root,directory)
            print(json.dumps({"stage":name,"status":"completed"},ensure_ascii=False),flush=True)
    else: result=actions[args.action](root,directory)
    if args.action in ("evaluate","all"):
        report=json.loads((directory / "report.json").read_text(encoding="utf-8"))
        print(json.dumps({"directory":str(directory),"primary_metrics":{k:v for k,v in report["metrics"].items() if k.startswith("10/")}},indent=2))
    else: print(json.dumps(result,indent=2))


if __name__=="__main__": main()
