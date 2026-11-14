"""Export a portable synthetic evidence pack; never publish or call a model."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    paths={
        'report.html':Path('runs/stage5-live-v4/report.html'),
        'acceptance.json':Path('runs/stage5-live-v4-audit/acceptance.json'),
        'comparison.json':Path('runs/stage5-live-v4-audit/comparison.json'),
        'costs.json':Path('runs/stage5-live-v4-audit/costs.json'),
        'scores.json':Path('runs/stage5-live-v4/results.json'),
        'frozen-manifest.json':Path('runs/stage5-freeze-v4/manifest.json'),
        'failures/v2-tool-budget.json':Path('runs/stage5-live-v2/raw-07/adaptive/result.json'),
        'failures/v3-coverage-action.json':Path('runs/stage5-live-v3/raw-10/all_context/result.json'),
    }
    for arm in ('all_context','adaptive'):
        directory=Path('runs/stage5-live-v4/raw-10')/arm
        for source in [directory/'result.json',directory/'score.json',directory/'usage.json',
                       *sorted(directory.glob('request-*.json')),*sorted(directory.glob('response-*.json'))]:
            paths[f'raw-10/{arm}/{source.name}']=source
    manifest={}
    for name,source in paths.items():
        destination=a.output/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
        manifest[name]=dict(source=source.as_posix(),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(files=len(paths),output=str(a.output),model_requests=0)))


if __name__=='__main__':main()
