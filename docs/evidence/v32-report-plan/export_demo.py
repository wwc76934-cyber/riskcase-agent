"""Export saved development evidence into a standalone local review demo."""
from pathlib import Path
import hashlib,json

ROOT=Path(__file__).resolve().parents[3]

def main():
    scope=ROOT/'runs/v32-report-plan-proposal-v1'; live=scope/'live'
    summary=json.loads((live/'recovery-summary-001.json').read_text(encoding='utf-8'))
    rows=[]
    for path in sorted((live/'cases').glob('*.json')):
        result=json.loads(path.read_text(encoding='utf-8'))
        rows.append(dict(case_id=result['case_id'],repeat=result['repeat'],
            result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),phases=result['runtime']['phases']))
    data=json.dumps(dict(summary=summary,cases=rows),ensure_ascii=False).replace('<','\\u003c').replace('&','\\u0026')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RiskCase｜开发可靠性复验审阅</title>
<style>body{font:16px/1.65 system-ui,sans-serif;background:#f4f6f8;color:#17293c;margin:0}main{max-width:1120px;margin:32px auto;padding:0 24px}h1{font-size:28px}small,.muted{color:#536477}section{background:white;border:1px solid #d8e1e9;border-radius:12px;padding:20px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:12px;font:13px/1.6 ui-monospace,monospace}select,input,textarea,button{font:inherit;padding:9px;max-width:100%;box-sizing:border-box}select,textarea,input{border:1px solid #aab8c5;border-radius:5px}button{cursor:pointer;background:#164d72;color:white;border:0;border-radius:5px;margin:4px}blockquote{border-left:3px solid #7c9db4;margin:8px 0;padding:8px 16px;background:#f8fafb}.bad{color:#a02c2c}.good{color:#146348}label{display:block;margin-top:10px}textarea{width:100%;min-height:80px}.pill{display:inline-block;padding:3px 9px;background:#eaf0f5;border-radius:16px}</style>
<main><h1>支付欺诈告警调查 · 可靠性审阅</h1>
<p>材料均为自建模拟开发案。本页回放已保存的真实模型运行，不调用模型、不查询外部系统、不执行金融操作。</p>
<section id="totals"></section>
<section><label>案件与预定重复 <select id="case"></select></label><div id="stages"></div><p id="position"></p></section>
<section><h2>当时取得的原件</h2><p class="muted">仅展示该阶段实际取得的证据；来源原文是待核查材料。</p><div id="originals"></div></section>
<section><h2>取证方式与执行记录</h2><p>本批为宿主确定性取证＋两阶段模型报告，模型自主工具调用为0。下方记录不能称为动态Agent计划。</p><pre id="plan"></pre></section>
<section><h2>已接受的事实与报告</h2><div id="report"></div></section>
<section><h2>本机演示审阅</h2><p>填写者只能记录自己的实际审阅。导出不会改变原始成绩、批准真实处置或满足独立业务专家审查。</p>
<label>审阅者姓名或代号 <input id="reviewer" autocomplete="off"></label>
<label>意见 <select id="decision"><option value="needs_more_evidence">需要更多证据</option><option value="agree_with_caveats">认可当前证据整理，但保留限制</option><option value="disagree">不同意当前报告</option></select></label>
<label>具体理由 <textarea id="reason"></textarea></label>
<label><input type="checkbox" id="confirmed"> 我实际阅读了当前阶段原件与报告；这是我的审阅意见。</label>
<button id="export">导出当前阶段审阅JSON</button><p id="notice" role="status"></p></section>
<small>客户来源立场不等于实际授权、确认欺诈或法律责任。正文语义未获全面验证。默认产品仍为B0。</small></main>
<script type="application/json" id="data">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id);
let index=0,stage=0;
function node(tag,text){const e=document.createElement(tag);e.textContent=text;return e}
function pretty(v){return JSON.stringify(v,null,2)}
const s=data.summary;
$('totals').textContent=`总体交付 ${s.delivered}/36；完整案次 ${s.complete_delivered_cases}/12；T1/T2客户状态 ${s.source_statement_correct_T1_T2}/24。失败及未执行阶段均保留在分母中。`;
data.cases.forEach((c,i)=>{const e=node('option',`${c.case_id} · 重复${c.repeat}`);e.value=i;$('case').append(e)});
function render(){const c=data.cases[index],p=c.phases.find(p=>p.stage===stage);$('stages').replaceChildren();
 for(let t=0;t<3;t++){const b=node('button','T'+t);b.onclick=()=>{stage=t;render()};$('stages').append(b)}
 $('position').textContent=p?`当前时点 ${p.as_of} · ${p.report?'报告已交付':'未交付'}`:'该阶段因前期中断未执行，计入总体未成功。';
 $('originals').replaceChildren();$('report').replaceChildren();$('plan').textContent=p?pretty({plan:p.plan,error:p.error,requests:p.protocol_request_attempts,source_tool_attempts:p.source_tool_attempts}):'没有执行记录';
 if(p){for(const [id,e] of Object.entries(p.evidence||{})){const d=node('details','');d.append(node('summary',id+' · 取得的原件/工具结果'));d.append(node('pre',pretty(e)));$('originals').append(d)}
 if(p.report){$('report').append(node('p',p.report.summary));$('report').append(node('p','目标客户来源状态：'+p.report.source_statement.value));
 for(const f of p.report.facts||[]){const d=node('div','');d.append(node('strong',`${f.id} · ${f.predicate} = ${f.value}`));for(const cite of f.citations||[]){d.append(node('blockquote',`${cite.source_id}，第${cite.line}行：${cite.text}`))}$('report').append(d)}
 const d=node('details','');d.append(node('summary','完整已接受报告'));d.append(node('pre',pretty(p.report)));$('report').append(d)}
 else{$('report').append(node('p','报告未被宿主接受。不能用格式失败前的内容冒充成功交付。'))}}
 else{$('report').append(node('p','无报告，也没有补造事实。'))}
 $('confirmed').checked=false;$('reason').value='';$('notice').textContent='';}
$('case').onchange=()=>{index=Number($('case').value);stage=0;render()};
$('export').onclick=()=>{const reviewer=$('reviewer').value.trim(),reason=$('reason').value.trim();if(!reviewer||!reason||!$('confirmed').checked){$('notice').textContent='请填写实际审阅者、具体理由并确认已阅读。';return}
const c=data.cases[index];const record={schema:'riskcase-local-demo-review-v1',case_id:c.case_id,repeat:c.repeat,stage,result_sha256:c.result_sha256,reviewer,decision:$('decision').value,reason,actual_reader_attested:true,independent_expert_review:false,changes_original_score:false,authorizes_financial_action:false,created_at:new Date().toISOString()};
const url=URL.createObjectURL(new Blob([pretty(record)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`review-${c.case_id}-r${c.repeat}-T${stage}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('notice').textContent='已导出本机演示审阅意见；未修改原始实验。'};render();
</script></html>'''.replace('__DATA__',data)
    out=ROOT/'runs/v32-reliability-demo-v1';out.mkdir(exist_ok=False)
    (out/'index.html').write_text(page,encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(dict(schema='riskcase-saved-development-demo-v1',
        html_sha256=hashlib.sha256((out/'index.html').read_bytes()).hexdigest(),
        source_summary_sha256=hashlib.sha256((live/'recovery-summary-001.json').read_bytes()).hexdigest(),
        cases=len(rows),external_model_requests=0,human_review_fabricated=False),indent=2),encoding='utf-8')
    print(str(out/'index.html'))

if __name__=='__main__':main()
