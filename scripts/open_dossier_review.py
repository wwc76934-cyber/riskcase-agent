"""Open a COPY of completed saved investigations; no model requests or source edits."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Saved execution directory containing harness/jobs.sqlite')
    p.add_argument('--directory',type=Path,required=True,help='New isolated review workbench directory')
    p.add_argument('--port',type=int,default=8777)
    p.add_argument('--prepare-only',action='store_true')
    a=p.parse_args()
    if not 1024<=a.port<=65535:p.error('Port must be 1024..65535')
    source=(a.source/'harness/jobs.sqlite').resolve()
    if not source.is_file():p.error('Saved investigation not found')
    if a.directory.exists():p.error('Use a new review directory; original approvals will not be overwritten')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT state,spec FROM jobs').fetchall()
        if not rows or any(r[0] in ('queued','running') for r in rows):
            p.error('Only finished or stopped runs can be copied; do not restart an active model job')
        destination=a.directory/'investigations'
        destination.mkdir(parents=True,exist_ok=False)
        with sqlite3.connect(destination/'jobs.sqlite') as target:db.backup(target)
    print(json.dumps(dict(url=f'http://127.0.0.1:{a.port}/investigations/page',
                         token_file=str(a.directory/'auth.token'),original_unchanged=str(source),
                         model_execution=False,source_modes=sorted({json.loads(r[1])['mode'] for r in rows})),ensure_ascii=False),flush=True)
    if not a.prepare_only:
        return subprocess.call([sys.executable,'-X','utf8','-B','-m','app.cli.workbench','--directory',str(a.directory),
                                '--demo-data',str(a.directory/'demo-data'),'--port',str(a.port)])
    return 0


if __name__=='__main__':raise SystemExit(main())
