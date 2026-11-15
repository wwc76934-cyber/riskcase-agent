'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),output=path.join(root,'runs',process.env.GROUP_QA_OUTPUT||'stage6d-group-browser-v1');
assert.equal(fs.existsSync(output),false,'Use a new output');fs.mkdirSync(output,{recursive:true});
const source=path.join(root,'runs/stage6d-group-demo-v1'),group=JSON.parse(fs.readFileSync(path.join(source,'group.json'),'utf8'));
const python=process.env.RISKCASE_QA_PYTHON;if(!python)throw Error('Python path required');
const base='http://127.0.0.1:8781',log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port','8781','--directory',path.join(output,'service'),'--catalog',path.join(source,'catalog.json')],{cwd:root,env:{...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)},windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',page_errors:[],external_requests:[],checks:[],model_calls:0,controller:'browser_test_NOT_independent_human',group_members:group.prepared_members.length};let browser;
async function main(){
 for(let i=0;i<150;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,200))}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));
 await page.goto(base+'/investigations/page');await page.locator('#token').fill(token);await page.locator('#fraud-refresh').click();
 await page.locator('#fraud-source').filter({hasText:'已加载 '+group.prepared_members.length}).waitFor();
 await page.locator('#fraud-group').selectOption(group.group.group_id);
 assert.equal(await page.locator('#fraud-case option').count(),group.prepared_members.length);
 assert.deepEqual((await page.locator('#fraud-case option').allTextContents()).map(t=>Number(t.match(/排名 (\d+)/)[1])),group.group.members.map(r=>r.day_rank));
 const jobs=[];
 async function start(member){
  await page.locator('#fraud-case').selectOption(member.case_id);
  const submitted=page.waitForResponse(r=>r.url()===base+'/fraud-investigations'&&r.request().method()==='POST');
  await page.locator('#fraud-start').click();const response=await submitted;assert.equal(response.status(),200);const created=await response.json();
  let row;
  for(let i=0;i<300;i++){row=await page.evaluate(id=>api('/investigations/'+id),created.id);if(row.state==='awaiting_review')break;assert.ok(['queued','running'].includes(row.state),JSON.stringify({state:row.state,reason:row.reason}));await new Promise(r=>setTimeout(r,200))}
  assert.equal(row.state,'awaiting_review');await page.evaluate(id=>select(id),created.id);
  const result={id:created.id,state:row.state,transaction:row.result.validation.alert.transaction,client:row.result.client_kind,evidence:Object.keys(row.checkpoint.evidence).length,ticket:row.ticket};
  assert.equal(result.transaction.transaction_id,member.transaction_id);assert.equal(result.transaction.event_time,member.cutoff);
  assert.equal(result.client,'simulation');assert.equal(result.evidence,5);assert.equal(result.ticket,null);return result;
 }
 for(const member of group.prepared_members)jobs.push(await start(member));
 assert.equal(new Set(jobs.map(j=>j.id)).size,group.prepared_members.length);
 receipt.current_fact_evidence=jobs.reduce((n,j)=>n+j.evidence,0);receipt.distinct_fact_cutoffs=new Set(jobs.map(j=>j.transaction.event_time)).size;
 receipt.checks.push('all_group_members_individually_run_with_real_mcp','each_target_and_cutoff_preserved');
 await page.evaluate(id=>select(id),jobs[0].id);await page.locator('#draft').fill('逐笔核对行为线索与授权缺口；此批准不影响同组其他交易。');await page.locator('#save').click();
 await page.locator('#receipt').filter({hasText:'草稿已保存'}).waitFor();for(const box of await page.locator('.semantic-check').all())await box.check();
 await page.locator('#approve').click();await page.locator('#receipt').filter({hasText:'已批准'}).waitFor();
 const approved=await page.evaluate(async id=>(await api('/investigations/'+id)).ticket,jobs[0].id);assert.equal(approved.approved_hash,approved.hash);
 for(const job of jobs.slice(1))assert.equal(await page.evaluate(async id=>(await api('/investigations/'+id)).ticket,job.id),null);
 const reopened=await start(group.prepared_members[0]);assert.notEqual(reopened.id,jobs[0].id);
 assert.equal((await page.evaluate(async id=>(await api('/investigations/'+id)).ticket,jobs[0].id)).approved_hash,approved.hash);
 receipt.checks.push('approval_is_per_transaction_not_group','new_review_does_not_overwrite_or_inherit_old_approval');
 await page.locator('#fraud-group').selectOption('');assert.equal(await page.locator('#fraud-case option').count(),group.prepared_members.length);
 await page.locator('#fraud-members').screenshot({path:path.join(output,'group-members.png')});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:path.join(output,'mobile.png')});
 assert.deepEqual(receipt.page_errors,[]);assert.deepEqual(receipt.external_requests,[]);
 receipt.task_runs=jobs.length+1;receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();fs.closeSync(log);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
