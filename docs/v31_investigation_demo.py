"""Build a local evidence/review demo from the two real MCP scripted fixtures."""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


PAGE = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>RiskCase Agent 调查复盘</title>
<style>
body{font:16px system-ui,sans-serif;background:#f5f7fb;color:#182432;margin:0}
main{max-width:1024px;margin:auto;padding:24px} section{background:white;border:1px solid #dce3eb;padding:20px;margin:16px 0;border-radius:12px}
label{display:block;margin:10px 0}select,textarea,button{font:inherit;padding:8px}textarea{width:96%;min-height:80px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:12px;background:#f1f5fa;border-radius:6px}
.note{color:#344a61}.stamp{word-break:break-all}button{cursor:pointer}
</style><main>
<header><h1>RiskCase Agent 调查复盘</h1>
<p class="note">作者模拟案卷。取证通过真实本机 MCP 执行，规划与报告由离线脚本驱动。人工意见可在本页填写并导出。</p></header>
<label>调查轨迹 <select id="case"></select></label>
<label>资料到达阶段 <select id="stage"><option value="0">T0</option><option value="1" selected>T1</option><option value="2">T2</option></select></label>
<section><h2>取证计划与变化</h2><pre id="plan"></pre></section>
<section><h2>已取得原件和缺失回执</h2><div id="evidence"></div></section>
<section><h2>调查报告</h2><pre id="report"></pre><small class="stamp" id="hash"></small></section>
<section><h2>人工审阅</h2><p>审阅意见只绑定本页报告指纹并保存到本机。</p>
<form id="review"><label>处理建议 <select id="decision"><option value="retain_review">保留人工复核</option>
<option value="request_more_evidence">请求补充资料</option><option value="return_for_correction">退回修正</option></select></label>
<label>依据和待核实事项<textarea id="notes" required maxlength="2000"></textarea></label>
<button>导出审阅 JSON</button></form></section>
</main><script id="data" type="application/json">__DATA__</script>
<script>
const get = id => document.getElementById(id);
const records = JSON.parse(get('data').textContent);
const caseSelect = get('case'), stageSelect = get('stage');
for(const [i,item] of records.entries()) {
  const option = new Option(item.title, String(i)); caseSelect.add(option);
}
const chosen = () => records[Number(caseSelect.value)].phases[Number(stageSelect.value)];
function render() {
  const item = chosen();
  const trace = item.plan?.trace ?? [];
  get('plan').textContent = trace.length ? trace.map(row =>
    [row.purpose, row.hypothesis_to_test, JSON.stringify(row.query_outcomes ?? row.source_ids)].join('\n')
  ).join('\n\n') : '此阶段没有新增模型选择的取证步骤。';
  get('report').textContent = JSON.stringify(item.report, null, 2);
  get('hash').textContent = 'SHA256 ' + item.report_sha256;
  get('evidence').replaceChildren();
  for(const frame of item.evidence) {
    for(const source of frame.originals) {
      const details = document.createElement('details');
      const title = document.createElement('summary');
      title.textContent = [source.source_id, source.business_role, source.available_at].join(' · ');
      const lines = document.createElement('pre');
      lines.textContent = source.lines.map(line => line.number + '  ' + line.text).join('\n');
      details.append(title, lines); get('evidence').append(details);
    }
    for(const receipt of frame.unavailable) {
      const line = document.createElement('p');
      line.textContent = receipt.source_id + ' · ' + receipt.status + ' · 模拟费用 ' + receipt.cost_units;
      get('evidence').append(line);
    }
  }
}
caseSelect.addEventListener('change', render); stageSelect.addEventListener('change', render);
get('review').addEventListener('submit', event => {
  event.preventDefault();
  const item = chosen(), notes = get('notes').value.trim();
  if(!notes) return;
  const result = {schema:'riskcase-demo-review-v1', case_id:item.case_id, stage:item.stage,
    report_sha256:item.report_sha256, decision:get('decision').value, notes,
    source:'locally_entered_demo_review', disposition_executed:false};
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type:'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = item.case_id + '-review.json';
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
render();
</script></html>
"""


def read_trajectory(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=[json.loads(body) for (body,) in db.execute('SELECT body FROM v3_case_phases ORDER BY stage')]
    if len(rows)!=3 or any(row['condition']!='B2' or row['evidence_kind']!='scripted_protocol_probe'
                           or row['report'] is None for row in rows):
        raise ValueError('Three-stage scripted B2 demo required')
    result=[]
    for row in rows:
        report=row['report']
        frames=[entry['result']['data'] for entry in row['evidence'].values()]
        evidence=[dict(originals=data.get('B',[]),unavailable=[q for q in data.get('query_results',[])
                         if q['status']!='available']) for data in frames]
        encoded=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        result.append(dict(case_id=report['case_id'],stage=row['stage'],report=report,plan=row['plan'],
                           evidence=evidence,report_sha256=hashlib.sha256(encoded).hexdigest()))
    return dict(title=result[0]['case_id'],phases=result)


def build(paths, output):
    data=json.dumps([read_trajectory(path) for path in paths],ensure_ascii=False)
    safe=data.replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:stream.write(PAGE.replace('__DATA__',safe))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('databases',type=Path,nargs='+')
    args=parser.parse_args()
    build(args.databases,args.output)
