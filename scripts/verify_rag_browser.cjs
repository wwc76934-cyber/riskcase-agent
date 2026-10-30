'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),output=path.join(root,'runs',process.env.RAG_QA_OUTPUT||'rag-stage3-browser-01');fs.mkdirSync(output,{recursive:true});
const python=process.env.RISKCASE_QA_PYTHON;if(!python)throw Error('Host must provide Python interpreter');
const base='http://127.0.0.1:8776',log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port','8776','--directory',path.join(output,'service'),'--demo-data',path.join(output,'data')],{cwd:root,env:{...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)},windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',page_errors:[],external_requests:[],checks:[]};let browser;
async function main(){
 for(let i=0;i<150;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,200))}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1100},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));await page.goto(base+'/investigations/page');
 await page.locator('#token').fill(token);await page.locator('#start').click();
 await page.locator('#state').filter({hasText:'报告等待审阅'}).waitFor({timeout:60000});
 await page.locator('#methods-refresh').click();await page.locator('#method-head').filter({hasText:'当前版本：0'}).waitFor();
 await page.locator('#method-order').selectOption('merchant,settlement,coverage');await page.locator('#method-create').click();
 await page.waitForFunction(()=>!document.getElementById('method-evaluate').disabled);await page.locator('#method-evaluate').click();
 await page.locator('#method-candidate').filter({hasText:'任务回归：拒绝'}).waitFor();assert.equal(await page.locator('#method-publish').isDisabled(),true);
 receipt.checks.push('unsafe_precedence_candidate_rejected_by_task_decisions');
 await page.locator('#method-order').selectOption('coverage,settlement,merchant');await page.locator('#method-create').click();
 await page.waitForFunction(()=>!document.getElementById('method-evaluate').disabled);await page.locator('#method-evaluate').click();
 await page.locator('#method-candidate').filter({hasText:'通过（16/16）'}).waitFor();await page.locator('#method-publish').click();
 await page.locator('#method-head').filter({hasText:'当前版本：1'}).waitFor();assert.match(await page.locator('#knowledge').innerText(),/固定经验版本：0/);
 receipt.checks.push('reviewed_revision_published_existing_task_still_release_0');
 await page.locator('#case').selectOption('case-04');await page.locator('#goal').fill('Investigate evidence conflict');await page.locator('#start').click();
 await page.locator('#progress').filter({hasText:'evidence-conflict'}).waitFor({timeout:60000});await page.locator('#state').filter({hasText:'报告等待审阅'}).waitFor({timeout:60000});
 await page.locator('#methods-refresh').click();await page.locator('#knowledge').filter({hasText:'M:release-1'}).waitFor();
 assert.match(await page.locator('#answer').innerText(),/需要人工进一步调查/);receipt.checks.push('new_task_consumes_frozen_D_and_M_advisory_context_with_E_fact_conclusion');
 await page.locator('#method-rollback').click();await page.locator('#method-head').filter({hasText:'当前版本：2'}).waitFor();
 assert.match(await page.locator('#method-head').innerText(),/没有已发布经验/);assert.match(await page.locator('#knowledge').innerText(),/固定经验版本：1/);
 receipt.checks.push('rollback_removes_future_memory_preserves_old_task_snapshot');
 await page.locator('#knowledge').evaluate(el=>el.style.maxHeight='220px');await page.locator('#knowledge').evaluate(el=>el.style.overflow='auto');
 await page.locator('section').last().screenshot({path:path.join(output,'knowledge.png')});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.external_requests,[]);assert.deepEqual(receipt.page_errors,[]);receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();fs.closeSync(log);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
