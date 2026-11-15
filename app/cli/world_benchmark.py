"""Run the deterministic baseline without model calls; preserve all outcomes."""
import argparse
import json
from pathlib import Path
from app.evaluation.world_runner import run_b0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fold',choices=['development','frozen','challenge'],default='development')
    args=p.parse_args();result=run_b0(args.dataset,args.output,args.fold)
    print(json.dumps({k:v for k,v in result.items() if k!='results'}))


if __name__=='__main__':main()
