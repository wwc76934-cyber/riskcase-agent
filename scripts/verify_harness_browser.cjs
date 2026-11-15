'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),output=path.join(root,'runs/harness-stage2-browser');fs.mkdirSync(output,{recursive:true});
const python=process.env.RISKCASE_QA_PYTHON;if(!python)throw Error('Host must provide Python interpreter');
const base='http://127.0.0.1:8774',log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port','8774','--directory',path.join(output,'service'),'--demo-data',path.join(output,'data')],{cwd:root,env:{...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)},windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',page_errors:[],external_requests:[],checks:[]};let browser;
async function main(){
 for(let i=0;i<120;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,200))}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1100},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));await page.goto(base);
 await page.getByRole('link',{name:/Skill 与 Harness/}).click();await page.locator('#token').fill(token);await page.locator('#start').click();
 await page.locator('#state').filter({hasText:'报告等待审阅'}).waitFor({timeout:60000});
 assert.match(await page.locator('#progress').innerText(),/merchant-change/);receipt.checks.push('workbench_to_real_mcp_investigation_and_loaded_skill');
 await page.locator('#draft').fill('请分析师复核商户业务变化证据。');await page.locator('#save').click();
 await page.waitForFunction(()=>!document.getElementById('approve').disabled);await page.locator('#approve').click();await page.waitForFunction(()=>!document.getElementById('send').disabled);
 await page.locator('#draft').fill('更新工单：还需核对原始引用范围。');assert.equal(await page.locator('#send').isDisabled(),true);
 await page.locator('#save').click();await page.waitForFunction(()=>!document.getElementById('approve').disabled);assert.equal(await page.locator('#send').isDisabled(),true);
 receipt.checks.push('editing_and_saving_invalidates_old_approval');await page.locator('#approve').click();await page.waitForFunction(()=>!document.getElementById('send').disabled);
 await page.locator('#send').click();await page.waitForFunction(()=>document.getElementById('receipt').dataset.status==='submitted');
 const first=await page.locator('#receipt').getAttribute('data-ticket-id');
 await page.locator('#send').click();await page.waitForTimeout(500);assert.equal(await page.locator('#receipt').getAttribute('data-ticket-id'),first);
 receipt.checks.push('approved_content_submitted_once_to_local_mock');receipt.ticket_id=first;
 await page.locator('section').last().screenshot({path:path.join(output,'ticket.png')});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.external_requests,[]);assert.deepEqual(receipt.page_errors,[]);receipt.checks.push('no_external_requests_errors_or_mobile_overflow');receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();fs.closeSync(log);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
