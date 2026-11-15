'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),report=path.resolve(root,process.argv[2]||'runs/multisource-stage1-offline-final/report.html');
if(!report.startsWith(path.join(root,'runs')+path.sep))throw Error('Report must be in project runs directory');
const output=path.join(root,'runs/multisource-browser-stage1');fs.mkdirSync(output,{recursive:true});
const receipt={status:'running',checks:[],page_errors:[],external_requests:[]};let browser;
async function main(){
 const executablePath=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
 browser=await pw.chromium.launch({executablePath,headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block'});
 await context.route('**/*',async route=>{if(!route.request().url().startsWith('file:')){receipt.external_requests.push(route.request().url());await route.abort()}else await route.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));await page.goto(pathToFileURL(report).href);
 assert.ok(await page.locator('article').count()>=12);assert.match(await page.locator('h1').innerText(),/多来源证据调查/);
 await page.locator('details summary').first().click();assert.equal(await page.locator('details').first().getAttribute('open'),'');
 receipt.checks.push('case_reports_tool_traces_and_expandable_source_evidence');
 await page.locator('details summary').first().click();await page.screenshot({path:path.join(output,'report.png')});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.page_errors,[]);assert.deepEqual(receipt.external_requests,[]);
 receipt.checks.push('mobile_layout_no_page_errors_or_external_requests');receipt.status='passed';
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{
 if(browser)await browser.close();fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));
});
