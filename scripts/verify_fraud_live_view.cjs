'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),source=path.join(root,'runs/stage6b-fraud-live-v4');
const output=path.join(root,'runs',process.env.LIVE_VIEW_QA_OUTPUT||'stage6b-live-view-v1');assert.equal(fs.existsSync(output),false);
const directory=path.join(output,'service/investigations');fs.mkdirSync(directory,{recursive:true});
fs.copyFileSync(path.join(source,'baseline/service/investigations/jobs.sqlite'),path.join(directory,'jobs.sqlite'));
const manifest=JSON.parse(fs.readFileSync(path.join(source,'prepared.json'),'utf8'));
const python=process.env.RISKCASE_QA_PYTHON;if(!python)throw Error('Python path required');
const base='http://127.0.0.1:8783',log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port','8783','--directory',path.join(output,'service'),'--catalog',path.join(source,'baseline/catalog.json')],{cwd:root,env:{...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)},windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',page_errors:[],external_requests:[],displayed_provider_reports:0,new_model_calls:0,new_jobs:0};let browser;
async function main(){
 for(let i=0;i<150;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,200))}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));
 await page.goto(base+'/investigations/page');await page.locator('#token').fill(token);await page.locator('#refresh').click();
 const before=await page.evaluate(async()=>(await api('/investigations')).jobs.length);assert.equal(before,12);
 for(const job of manifest.jobs){
  await page.evaluate(id=>select(id),job.job);
  assert.match(await page.locator('#mode').innerText(),/外部模型调用或回放记录/);
  assert.match(await page.locator('#answer').innerText(),/持卡人授权确认/);
  const row=await page.evaluate(id=>api('/investigations/'+id),job.job);
  assert.equal(row.state,'awaiting_review');assert.equal(row.result.client_kind,'configured_external_client');
  assert.equal(Object.keys(row.checkpoint.evidence).length,5);assert.equal(row.ticket,null);
  if(job.profile==='full'){assert.equal(row.result.review_memory_snapshot.recalled,true);assert.match(await page.locator('#fm-snapshot').innerText(),/可复用记录 1 条/)}
  receipt.displayed_provider_reports++;
 }
 assert.equal(await page.evaluate(async()=>(await api('/investigations')).jobs.length),before);
 await page.locator('#answer').screenshot({path:path.join(output,'provider-report.png')});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.page_errors,[]);assert.deepEqual(receipt.external_requests,[]);receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();fs.closeSync(log);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
