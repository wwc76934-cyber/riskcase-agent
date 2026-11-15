"""Offline human review pack. Creating a pack does not constitute review."""
from pathlib import Path
import hashlib,json

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def build(dataset,output):
    dataset,output=Path(dataset),Path(output)
    if output.exists():raise FileExistsError('New human review pack required')
    fingerprint=sha(dataset/'dataset.json');manifest=load(dataset/'dataset.json')
    if fingerprint!=load(dataset/'dataset-sha256.json')['sha256']:raise ValueError('Dataset changed')
    entries=[e for e in manifest['entries'] if e['fold']=='evaluation'];cases=[]
    if len(entries)<40:raise ValueError('At least forty evaluation candidates required')
    for e in entries:
        for field in ('runtime','reference'):
            if (dataset/e[field]).resolve().parent!=(dataset/('runtime' if field=='runtime' else 'controller')).resolve() or sha(dataset/e[field])!=e[field+'_sha256']:raise ValueError('Review input changed')
        reference=load(dataset/e['reference'])
        if reference.get('reference_schema')!='v3-author-reference-v2':raise ValueError('Current author references required')
        cases.append(dict(case_id=e['case_id'],family=e['family'],runtime_sha256=e['runtime_sha256'],reference_sha256=e['reference_sha256'],runtime=load(dataset/e['runtime']),reference=reference))
    packet=dict(schema='v3-human-review-pack-v1',dataset_sha256=fingerprint,review_type='Human review of visible author references, NOT blind annotation',
        independently_reviewed=False,expert_reviewed=False,formal_frozen=False,cases=cases)
    output.mkdir(parents=True);write(output/'packet.json',packet)
    form=dict(schema='v3-human-review-response-v1',dataset_sha256=fingerprint,packet_sha256=sha(output/'packet.json'),reviewer=dict(name='',background='',human=False,not_an_author=False),
        decisions=[dict(case_id=c['case_id'],runtime_sha256=c['runtime_sha256'],reference_sha256=c['reference_sha256'],status='pending',notes='') for c in cases])
    write(output/'review-form.json',form)
    embedded=json.dumps(dict(packet=packet,form=form),ensure_ascii=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    (output/'review.html').write_text(HTML.replace('__DATA__',embedded),encoding='utf-8')
    (output/'README.md').write_text('请用浏览器打开 review.html，逐案查看各时间点原件与作者参考，填写身份、是否参与过创作及逐案意见，然后导出评审JSON。\n\n全部是自建模拟材料。页面不会联网；不会自动批准或声称专家真值。若您参与过创作，请勿勾选独立审阅声明。选择需修改的案件将阻止后续冻结，修改后需要重新生成并复核新版本。参考可见，属于参考审阅，不是盲标注。浏览器页面只在内存中保存编辑，关闭前请导出。\n',encoding='utf-8')
    return dict(cases=len(cases),dataset_sha256=fingerprint,packet_sha256=sha(output/'packet.json'),independent_person_reviewed=False,formal_frozen=False,external_model_requests=0)


def validate(pack,response):
    pack,response=Path(pack),Path(response);packet=load(pack/'packet.json');body=load(response)
    if body.get('schema')!='v3-human-review-response-v1' or body.get('dataset_sha256')!=packet['dataset_sha256'] or body.get('packet_sha256')!=sha(pack/'packet.json'):raise ValueError('Review fingerprint mismatch')
    reviewer=body.get('reviewer',{})
    if not isinstance(reviewer.get('name'),str) or not reviewer['name'].strip() or not isinstance(reviewer.get('background'),str) or not reviewer['background'].strip() or reviewer.get('human') is not True or reviewer.get('not_an_author') is not True:raise ValueError('Actual non-author human attestation required')
    decisions=body.get('decisions',[]);expected={c['case_id']:c for c in packet['cases']}
    if len(decisions)!=len(expected) or len({c['case_id'] for c in decisions})!=len(expected) or {c['case_id'] for c in decisions}!=set(expected):raise ValueError('Complete unique case review required')
    for d in decisions:
        case=expected[d['case_id']]
        if any(d.get(k)!=case[k] for k in ('runtime_sha256','reference_sha256')):raise ValueError('Reviewed case changed')
        if d.get('status')!='accepted' or not isinstance(d.get('notes'),str) or len(d['notes'].strip())<10:raise ValueError('Resolve every pending, unclear or revision decision and record a substantive note')
    return dict(status='complete_human_attestation_NOT_identity_verified',review_sha256=sha(response),dataset_sha256=packet['dataset_sha256'],packet_sha256=sha(pack/'packet.json'),
        reviewed_cases=len(decisions),independent_person_reviewed=True,independence_basis='Reviewer self-attestation; credentials not independently verified',
        external_expert_reviewed=False,blind_annotation=False,formal_frozen=False,reviewer=reviewer)


HTML='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RiskCase 人工评审</title>
<style>body{font:16px/1.65 system-ui;margin:auto;padding:24px;max-width:1080px;color:#182539;background:#f5f7fa}section,header{background:white;border:1px solid #d5dee8;border-radius:12px;padding:22px;margin-bottom:18px}h1{margin-top:0}input,select,textarea,button{font:inherit;padding:8px;margin:5px}textarea{box-sizing:border-box;width:100%;min-height:120px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f8;padding:14px}label{display:block}button{background:#174a83;color:white;border:0;border-radius:6px;cursor:pointer}.warning{color:#963d10}small{overflow-wrap:anywhere}</style>
<header><h1>RiskCase 调查任务人工评审</h1><p class="warning">全部为自建模拟原件和作者参考。尚未独立审阅、尚未正式冻结。本次审阅不是银行专家真值，也不是模型效果实验。</p><p>逐案检查：资料是否符合各时点权限；授权范围、转述、否定与纠正是否正确；关键反证与缺口是否充分；停止、重新调查和禁止动作是否合理。资料成本及时间均为模拟。</p><small id="fingerprint"></small></header>
<section><h2>审阅者</h2><label>姓名或稳定代号 <input id="name"></label><label>背景及业务经验 <input id="background" size="50"></label><label><input id="human" type="checkbox">我是实际参与逐案审阅的人。</label><label><input id="independent" type="checkbox">我未参与这些案例、模板和评分参考的创作。</label><p>请如实填写。非专家可参与参考审阅，不会被标记为外部专家。</p></section>
<section><label>选择案件 <select id="cases"></select></label><p id="progress"></p><div id="content"></div><label>审阅结论 <select id="status"><option value="pending">尚未完成</option><option value="accepted">参考可接受</option><option value="revise">需要修改</option><option value="unclear">无法判断</option></select></label><label>依据、问题及修改建议（每案至少10字）<textarea id="notes"></textarea></label><button id="previous">上一案</button><button id="next">下一案</button></section>
<section><button id="export">导出评审JSON</button><p>页面不联网，不自动提交。关闭前请导出，包含尚未完成的审阅也可以导出保存。只有全部参考经实际独立审阅并解决问题后，才可申请后续实验冻结。</p></section>
<script>const DATA=__DATA__;const P=DATA.packet,F=DATA.form;let current=0;const el=id=>document.getElementById(id);el('fingerprint').textContent='数据指纹：'+P.dataset_sha256;
P.cases.forEach((c,i)=>{let o=document.createElement('option');o.value=i;o.textContent=(i+1)+' / '+P.cases.length+' · '+c.case_id;el('cases').append(o)});
function flush(){const d=F.decisions[current];d.status=el('status').value;d.notes=el('notes').value;F.reviewer={name:el('name').value,background:el('background').value,human:el('human').checked,not_an_author:el('independent').checked}}
function block(title,value){let h=document.createElement('h3'),p=document.createElement('pre');h.textContent=title;p.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);el('content').append(h,p)}
function show(){let c=P.cases[current],r=c.runtime;el('cases').value=current;el('content').replaceChildren();block('调查范围与交易（P为目标，Q等为上下文；不是确认欺诈）',{case_id:c.case_id,alert:r.alert,transactions:r.transactions,data_kind:r.data_kind});
c.reference.stages.forEach((s,i)=>{block('T'+i+'：当时允许访问的模拟原件',r.records.filter(x=>s.allowed_sources.includes(x.source_id)));block('T'+i+'：待审阅的作者参考（可提出修改）',s)});block('始终不得向模型展示的未来原件编号',c.reference.never_visible_source_ids);el('status').value=F.decisions[current].status;el('notes').value=F.decisions[current].notes;el('progress').textContent='已标记接受 '+F.decisions.filter(d=>d.status==='accepted').length+' / '+P.cases.length+'；实际独立评审尚需导出与验证。'}
el('cases').onchange=()=>{flush();current=Number(el('cases').value);show()};el('previous').onclick=()=>{flush();current=Math.max(0,current-1);show()};el('next').onclick=()=>{flush();current=Math.min(P.cases.length-1,current+1);show()};el('export').onclick=()=>{flush();const u=URL.createObjectURL(new Blob([JSON.stringify(F,null,2)],{type:'application/json'}));let a=document.createElement('a');a.href=u;a.download='riskcase-human-review-'+P.dataset_sha256.slice(0,12)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};show();</script></html>'''
