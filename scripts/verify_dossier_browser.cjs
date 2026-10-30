'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),output=path.join(root,'runs',process.env.DOSSIER_QA_OUTPUT||'stage5-browser-v1');
fs.mkdirSync(output,{recursive:true});
const target=path.join(output,'service/investigations');assert.equal(fs.existsSync(target),false,'Use a fresh QA output');fs.mkdirSync(target,{recursive:true});
fs.copyFileSync(path.join(root,process.env.DOSSIER_QA_SOURCE||'runs/stage5-offline-v1/raw-01/all_context/harness/jobs.sqlite'),path.join(target,'jobs.sqlite'));
const python=process.env.RISKCASE_QA_PYTHON;if(!python)throw Error('Python path required');
const base='http://127.0.0.1:8779',log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port','8779','--directory',path.join(output,'service'),'--demo-data',path.join(output,'data')],{cwd:root,env:{...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)},windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',page_errors:[],external_requests:[],checks:[],model_calls:0};let browser;
async function main(){
 for(let i=0;i<150;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,200))}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:950},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));
 await page.goto(base+'/investigations/page');await page.locator('#token').fill(token);await page.locator('#refresh').click();
 await page.getByRole('button',{name:new RegExp(process.env.DOSSIER_QA_CASE||'raw-01')}).click();await page.locator('#answer').filter({hasText:'语义'}).count();
 await page.locator('#semantic-review').waitFor({state:'visible'});
 const answer=await page.locator('#answer').innerText();assert.match(answer,/净扣款/);assert.match(answer,/L3/);assert.match(answer,/policy-current/);assert.match(answer,/人工逐项复核/);
 if(process.env.DOSSIER_QA_CASE==='raw-10'){assert.match(await page.locator('#progress').innerText(),/settlement_coverage/);assert.doesNotMatch(await page.locator('#progress').innerText(),/无已声明缺口/);assert.match(answer,/已观察净扣款/);}
 receipt.checks.push('claim_values_next_to_original_lines_and_policy_with_semantic_warning');
 await page.locator('section').nth(1).screenshot({path:path.join(output,'claims.png')});
 await page.locator('#draft').fill('核对待处理的重复入账及排队退款，不自动关闭。');await page.locator('#save').click();
 await page.locator('#approve').waitFor({state:'visible'});await page.locator('#approve').click();
 await page.locator('#error').filter({hasText:'409'}).waitFor();assert.equal(await page.locator('#send').isDisabled(),true);
 receipt.checks.push('server_rejects_approval_without_explicit_semantic_review');
 for(const box of await page.locator('.semantic-check').all())await box.check();
 await page.locator('#approve').click();await page.locator('#receipt').filter({hasText:'已批准'}).waitFor();
 await page.locator('#send').click();await page.locator('#receipt').filter({hasText:'已提交'}).waitFor();
 receipt.checks.push('content_bound_review_and_simulated_ticket_complete');
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.page_errors,[]);assert.deepEqual(receipt.external_requests,[]);receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();fs.closeSync(log);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
