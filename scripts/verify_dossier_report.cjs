'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const root=path.resolve(__dirname,'..'),directory=path.resolve(root,process.argv[2]||'runs/stage5-offline-v2');
const output=path.join(directory,process.argv[3]||'browser-report-check');assert.equal(fs.existsSync(output),false,'Use a fresh check directory');fs.mkdirSync(output,{recursive:true});
const receipt={status:'running',page_errors:[],external_requests:[],model_calls:0};let browser;
async function main(){
 browser=await pw.chromium.launch({executablePath:['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync),headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block'});
 await context.route('**/*',async r=>{if(new URL(r.request().url()).protocol!=='file:'){receipt.external_requests.push(r.request().url());await r.abort()}else await r.continue()});
 const page=await context.newPage();page.on('pageerror',e=>receipt.page_errors.push(e.message));
 await page.goto(pathToFileURL(path.join(directory,'report.html')).href);
 const count=await page.locator('#choose option').count();assert.ok(count>0);assert.equal(await page.locator('.claim').count(),4);
 await page.locator('#choose').selectOption(String(count-1));assert.match(await page.locator('#claims').innerText(),/L[1-9]/);
 await page.screenshot({path:path.join(output,'report.png'),fullPage:true});
 await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(receipt.page_errors,[]);assert.deepEqual(receipt.external_requests,[]);receipt.status='passed';receipt.cases=count;
}
main().catch(e=>{receipt.status='failed';receipt.error=e.stack;process.exitCode=1}).finally(async()=>{if(browser)await browser.close();fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt,null,2));});
