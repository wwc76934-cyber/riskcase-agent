"""Render saved outputs only; never constructs a model client or changes experiments."""
import argparse
from html import escape
import json
from pathlib import Path


def render(directory, output):
    rows=json.loads((directory/'results.json').read_text(encoding='utf-8'))
    summary=json.loads((directory/'summary.json').read_text(encoding='utf-8'))
    data=[]
    for row in rows:
        result=json.loads((directory/row['case']/row['strategy']/'result.json').read_text(encoding='utf-8'))
        data.append(dict(metrics=row,result=result))
    encoded=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    table=''.join('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in [name,','.join(s['client_kinds']),f"{s['passed']}/{s['n']}",f"{s['facts_correct']}/{s['facts_total']}",s['mean_originals'],s['real_requests'],s['estimated_cny']])+'</tr>' for name,s in summary.items())
    html=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>原始案卷：对照与原文核对</title>
<style>body{font:16px/1.6 system-ui;overflow-wrap:anywhere;color:#17343e;background:#edf3f5;margin:0}main{max-width:1200px;padding:24px;margin:auto}section{background:white;padding:22px;margin:18px 0;border-radius:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd8db;padding:8px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}select{font:inherit;padding:10px;max-width:100%}.scroll{overflow:auto}.warning{color:#8a3714}.claim{border-left:4px solid #16786b;padding:12px;background:#eff7f5;margin:12px 0}button{padding:9px;cursor:pointer}</style>
<main><h1>原始案卷调查：结果与证据</h1><p>内部合成案例。模型结果、标注辅助接线检查分开标识。引用有效不代表语义正确；所有原文仍需审阅。</p>
<section class="scroll"><table><tr><th>条件</th><th>客户端</th><th>全项通过</th><th>事实值</th><th>平均原件</th><th>真实请求</th><th>估算元</th></tr>TABLE</table></section>
<section><label>选择案件与条件 <select id="choose"></select></label><p id="status"></p><div id="claims"></div><details><summary>完整运行指标和原始答案</summary><pre id="raw"></pre></details></section></main>
<script type="application/json" id="dataset">DATA</script><script>
const rows=JSON.parse(document.getElementById('dataset').textContent),$=id=>document.getElementById(id);
for(const [i,row] of rows.entries()){let o=document.createElement('option');o.value=i;o.textContent=row.metrics.case+' · '+row.metrics.strategy;$('choose').append(o)}
function source(ref,evidence,policy){const entry=evidence[ref.evidence_id]?.result;if(!entry)return '无效引用 '+ref.evidence_id;const docs=policy?entry.data.documents:[entry.data];return docs.map(d=>d.id+'\n'+(d.lines||[]).filter(l=>l.number>=ref.line_start&&l.number<=ref.line_end).map(l=>'L'+l.number+' '+l.text).join('\n')).join('\n')}
function show(){const row=rows[Number($('choose').value)],a=row.result.answer;$('status').textContent=row.metrics.client_kind+'；'+row.result.state+'；评分：'+(row.metrics.score.task_passed?'全项通过':'未通过（失败保留）');$('claims').replaceChildren();if(a){for(const c of a.claims||[]){let box=document.createElement('div');box.className='claim';let h=document.createElement('strong');h.textContent=c.name+'：'+c.value;let p=document.createElement('pre');p.textContent=c.citations.map(r=>source(r,row.result.evidence,false)).join('\n\n');box.append(h,p);$('claims').append(box)}let p=document.createElement('pre');p.textContent='建议：'+a.verdict+'\n缺口：'+a.missing.join('、')+'\n适用规程：\n'+source(a.policy,row.result.evidence,true);$('claims').append(p)}$('raw').textContent=JSON.stringify({metrics:row.metrics,answer:a,reason:row.result.reason,raw:row.result.raw},null,2)}
$('choose').onchange=show;show();</script></html>'''
    output.write_text(html.replace('TABLE',table).replace('DATA',encoded),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();render(a.directory,a.output)
