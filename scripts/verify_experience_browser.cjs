'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),output=path.join(root,'runs/experience-browser-v4-02');
fs.mkdirSync(output,{recursive:true});
const port=8773,base='http://127.0.0.1:'+port;
const python=process.env.RISKCASE_QA_PYTHON;
if(!python)throw Error('Host must supply the existing Python interpreter');
const env={...process.env,PYTHONPATH:[path.join(root,'.venv-service-addons'),path.join(root,'.venv/Lib/site-packages'),root].join(path.delimiter)};
const log=fs.openSync(path.join(output,'server.log'),'a');
const server=spawn(python,['-X','utf8','-B','-m','app.cli.workbench','--port',String(port),'--directory',path.join(output,'service'),'--demo-data',path.join(output,'data')],{cwd:root,env,windowsHide:true,stdio:['ignore',log,log]});
const receipt={status:'running',external_requests:[],page_errors:[],checks:[]};
let browser;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 for(let i=0;i<100;i++){try{if((await fetch(base)).ok)break}catch{}await pause(200)}
 const token=fs.readFileSync(path.join(output,'service/auth.token'),'utf8').trim();
 const executablePath=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
 browser=await pw.chromium.launch({executablePath,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));
 await page.goto(base);await page.locator('#token').fill(token);await page.locator('#connect').click();await page.locator('#workspace').waitFor();
 await page.locator('#submit').click();await page.locator('#state').filter({hasText:'报告等待审阅'}).waitFor({timeout:30000});
 await page.waitForFunction(()=>document.getElementById('knowledgeResult').textContent.includes('过程参考'));
 await page.getByRole('button',{name:'拒绝报告',exact:true}).click();await page.locator('#state').filter({hasText:'已拒绝'}).waitFor();
 await page.getByText('从当前已拒绝报告形成候选经验',{exact:true}).click();
 await page.locator('#lessonFeedback').fill('浏览器机制验证：分析师反馈要求核对观察窗口覆盖，非真实错误发现。');
 await page.locator('#lessonBody').fill('频次历史覆盖 frequency velocity incomplete observation：覆盖不完整且频次不足时应说明资料不足。');
 await page.locator('#lessonPropose').click();await page.locator('#lessonEvaluate').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.getElementById('lessonEvaluate').disabled);
 await page.locator('#lessonEvaluate').click();await page.waitForFunction(()=>!document.getElementById('lessonPromote').disabled);
 await page.locator('#lessonPromote').click();await page.waitForFunction(()=>{return document.getElementById('lessonResult').dataset.promoted==='yes'});
 receipt.checks.push('rejected_report_to_reviewed_gated_promotion');
 await page.locator('#knowledgeQuery').fill('velocity incomplete observation');await page.locator('#knowledgeSearch').click();
 await page.waitForFunction(()=>document.getElementById('knowledgeSearchResult').textContent.includes('K:coverage:v1'));
 receipt.checks.push('promoted_memory_retrievable_in_workbench');
 await page.locator('#lessonResult').screenshot({path:path.join(output,'promotion.png')});
 await page.setViewportSize({width:390,height:900});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.page_errors,[]);assert.deepEqual(receipt.external_requests,[]);
 receipt.checks.push('no_page_errors_external_requests_or_mobile_overflow');receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{
 if(browser)await browser.close();server.kill();fs.closeSync(log);
 fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));
});
