/* Isolated local development browser QA: no user profile, no model key, no browser download. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const output = path.resolve(root, process.env.RISKCASE_QA_OUTPUT || 'runs/workbench-ui-qa');
if (!output.startsWith(root + path.sep)) throw new Error('QA artifacts must remain inside the project');
const base = process.env.RISKCASE_QA_URL || 'http://127.0.0.1:8767';
const parsed = new URL(base);
if (parsed.hostname !== '127.0.0.1' || parsed.protocol !== 'http:') throw new Error('Local fixture server only');
const token = process.env.RISKCASE_QA_TOKEN || 't'.repeat(40); // Explicit test placeholder, never a live credential.
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const candidates = [
  process.env.RISKCASE_QA_BROWSER,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].filter(Boolean);
const executablePath = candidates.find(p => fs.existsSync(p));
if (!executablePath) throw new Error('No installed browser executable; downloads prohibited');
fs.mkdirSync(output, {recursive: true});
const receipt = {started_at: new Date().toISOString(), target: base, browser: executablePath,
  isolation: 'new_headless_browser_and_context_no_user_profile', screenshots: [], assertions: [],
  console_errors: [], page_errors: [], request_failures: [], model_api_key_used: false, live_enabled: false};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;

async function main() {
  browser = await playwright.chromium.launch({executablePath, headless: true,
    args: ['--disable-gpu', '--no-first-run', '--no-default-browser-check']});
  receipt.browser_version = browser.version();
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}, locale: 'zh-CN', serviceWorkers: 'block'});
  const page = await context.newPage();
  page.on('console', msg => { if (msg.type() === 'error') receipt.console_errors.push({text: msg.text(), location: msg.location()}); });
  page.on('pageerror', error => receipt.page_errors.push(error.message));
  page.on('requestfailed', request => receipt.request_failures.push({url: request.url(), error: request.failure()?.errorText}));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== parsed.origin) { await route.abort(); throw new Error('External request prohibited'); }
    await route.continue();
  });
  const headers = {Authorization: 'Bearer ' + token};
  async function getJob(id) { const response = await context.request.get(base + '/jobs/' + id, {headers}); assert.equal(response.status(), 200); return response.json(); }
  async function shot(name, fullPage = true) { const filename = name + '.png'; await page.screenshot({path: path.join(output, filename), fullPage}); receipt.screenshots.push(filename); }
  async function state(text) { await page.locator('#state').filter({hasText: text}).waitFor({timeout: 30000}); }
  async function submit(kind, question) {
    await page.locator('#kind').selectOption(kind);
    if (kind !== 'mixed') await page.locator('#question').fill(question);
    const response = page.waitForResponse(r => r.url() === base + '/jobs' && r.request().method() === 'POST');
    await page.locator('#submit').click();
    const received = await response;
    assert.equal(received.status(), 202);
    return (await received.json()).id;
  }
  async function selectJob(id) { await page.locator('#jobs .job').filter({hasText: id.slice(-8)}).click(); }

  await page.goto(base + '/', {waitUntil: 'networkidle'});
  assert.equal(await page.locator('#login').isVisible(), true);
  await shot('01-login');
  await page.locator('#token').fill(token);
  await page.locator('#connect').click();
  await page.locator('#workspace').waitFor();
  await page.locator('#case option').first().waitFor({state: 'attached'});
  assert.match(await page.locator('#caseInfo').innerText(), /自建演示案例/);
  receipt.login_mode_options = await page.locator('#mode').evaluate(select => [...select.options].map(option => ({value: option.value, disabled: option.disabled})));
  assert.equal(receipt.login_mode_options.find(option => option.value === 'live').disabled, true);
  receipt.assertions.push('login_fixture_provenance_and_live_disabled');

  const direct = await submit('mixed');
  await state('报告等待审阅');
  assert.match(await page.locator('#content pre').innerText(), /自建/);
  assert.equal((await getJob(direct)).state, 'awaiting_review');
  await shot('02-direct-report');
  await page.getByRole('button', {name: '已核对，接受报告', exact: true}).click();
  await state('报告已审阅');
  receipt.assertions.push('five_checks_report_fixture_provenance_and_report_acceptance');

  const natural = await submit('natural_v2', '查金额和新商户，不要查频次');
  await state('请确认调查范围');
  assert.equal(await page.locator('#content li').count(), 2);
  assert.doesNotMatch(await page.locator('#content ul').innerText(), /此前一小时|此前1小时/);
  const proposed = await getJob(natural);
  assert.equal(proposed.state, 'awaiting_plan_confirmation');
  await shot('03-scope-confirmation');
  await page.getByRole('button', {name: '确认只读调查范围', exact: true}).click();
  await state('报告等待审阅');
  assert.match(await page.locator('#content pre').innerText(), /自建/);
  const afterNatural = await getJob(natural);
  assert.equal(afterNatural.state, 'awaiting_review');
  assert.equal(afterNatural.result.policy_approved, false);
  assert.equal(afterNatural.result.deployed, false);
  await shot('04-natural-report');
  receipt.assertions.push('natural_v2_two_item_plan_explicit_confirmation_then_fixture_report');

  const unclear = await submit('natural_v2', '帮我看看异常');
  await state('需要明确问题');
  assert.match(await page.locator('#content').innerText(), /没有为本次问题执行调查查询/);
  const unclearSummary = await context.request.get(base + '/jobs/' + unclear + '/artifacts/summary.json', {headers});
  assert.equal((await unclearSummary.json()).tool_attempts, 0);
  await shot('05-clarification');
  const rejected = await submit('natural_v2', '冻结账户');
  await state('超出支持范围');
  const rejectedSummary = await context.request.get(base + '/jobs/' + rejected + '/artifacts/summary.json', {headers});
  assert.equal((await rejectedSummary.json()).tool_attempts, 0);
  receipt.assertions.push('ambiguous_and_restricted_questions_stop_without_fact_queries');

  const pending = await submit('natural_v2', '只查频次');
  await state('请确认调查范围');
  // Force a late response from A after selecting completed report B. It must not redraw B.
  await context.route(base + '/jobs/' + pending, async route => { await pause(900); await route.continue(); });
  await selectJob(direct); await state('报告已审阅');
  await selectJob(pending); await selectJob(direct);
  await pause(1200);
  assert.match(await page.locator('#title').innerText(), new RegExp(direct.slice(-8)));
  assert.equal(await page.getByRole('button', {name: '确认只读调查范围', exact: true}).count(), 0);
  await context.unroute(base + '/jobs/' + pending);
  receipt.assertions.push('late_selected_task_response_cannot_replace_current_report_or_actions');

  await selectJob(pending); await state('请确认调查范围');
  const cancellationRequests = [];
  await context.route(base + '/jobs/' + pending + '/cancel', async route => {
    cancellationRequests.push(route.request().url()); await pause(700); await route.continue();
  });
  await page.getByRole('button', {name: '取消任务', exact: true}).click();
  await selectJob(direct);
  await pause(1300);
  assert.deepEqual(cancellationRequests, [base + '/jobs/' + pending + '/cancel']);
  assert.equal((await getJob(pending)).state, 'cancelled');
  assert.equal((await getJob(direct)).state, 'completed');
  assert.match(await page.locator('#title').innerText(), new RegExp(direct.slice(-8)));
  await shot('06-task-switch-and-cancel');
  receipt.assertions.push('cancel_request_remains_bound_to_original_task_during_selection_change');
  await page.setViewportSize({width: 390, height: 900});
  await page.locator('#content').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await shot('07-mobile-report', false);
  receipt.assertions.push('narrow_viewport_report_has_no_horizontal_overflow');

  receipt.jobs = {direct, natural, unclear, rejected, cancelled: pending};
  assert.equal(receipt.page_errors.length, 0, 'Uncaught browser errors');
  assert.equal(receipt.console_errors.length, 0, 'Browser console errors');
  assert.equal(receipt.request_failures.length, 0, 'Failed browser requests');
  receipt.status = 'passed';
  page.removeAllListeners('requestfailed'); // Browser-context shutdown aborts are outside the active test.
  await context.close();
}

main().catch(error => { receipt.status = 'failed'; receipt.error = error.stack; process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); receipt.finished_at = new Date().toISOString();
    receipt.script_sha256 = crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
    receipt.screenshot_sha256 = Object.fromEntries(receipt.screenshots.map(name => [name, crypto.createHash('sha256').update(fs.readFileSync(path.join(output, name))).digest('hex')]));
    fs.writeFileSync(path.join(output, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); console.log(JSON.stringify(receipt, null, 2)); });
