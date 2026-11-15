'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');const {pathToFileURL}=require('node:url');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),output=path.join(root,'runs',process.env.STAGE4_QA_OUTPUT||'stage4-browser-audit');fs.mkdirSync(output,{recursive:true});
const target=path.join(output,'service/investigations');assert.equal(fs.existsSync(target),false,'Use a new output directory');
fs.mkdirSync(target,{recursive:true});
// The UI reads result/checkpoint state from SQLite; raw model files stay in the
// original immutable replay directory and need not be copied into this fixture.
fs.copyFileSync(path.join(root,'runs/stage4-host-replay-v2/e4-f10-v1/full_agent/harness/jobs.sqlite'),path.join(target,'jobs.sqlite'));
const python=process.env.RISKCASE_QA_PYTHON;if(!python)throw Error('Host must provide Python interpreter');
const base='http://127.0.0.1:8778',log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port','8778','--directory',path.join(output,'service'),'--demo-data',path.join(output,'data')],{cwd:root,env:{...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)},windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',page_errors:[],external_requests:[],checks:[],model_calls:0};let browser;
async function main(){
 for(let i=0;i<150;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,200))}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block'});
 await context.route('**/*',async r=>{const url=new URL(r.request().url());if(url.protocol!=='file:'&&url.origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));
 await page.goto(pathToFileURL(path.join(root,'runs/stage4-live-v1/report.html')).href);
 assert.equal(await page.locator('details').count(),12);assert.match(await page.locator('table').innerText(),/real_LLM/);
 assert.match(await page.locator('table').innerText(),/0\/3/);receipt.checks.push('original_failures_visible_in_real_model_report');
 await page.locator('table').screenshot({path:path.join(output,'live-results.png')});
 await page.goto(base+'/investigations/page');await page.locator('#token').fill(token);await page.locator('#refresh').click();
 await page.getByRole('button',{name:/e4-f10-v1/}).click();await page.locator('#answer').filter({hasText:'待复核事项'}).waitFor();
 assert.match(await page.locator('#answer').innerText(),/需要人工进一步调查/);assert.match(await page.locator('#progress').innerText(),/Select an applicable skill/);
 assert.equal(await page.locator('#send').isDisabled(),true);receipt.checks.push('supported_answer_and_open_plan_visible_pending_review_not_auto_approved');
 await page.locator('section').nth(1).screenshot({path:path.join(output,'planning-warning.png')});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.external_requests,[]);assert.deepEqual(receipt.page_errors,[]);receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();fs.closeSync(log);fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
