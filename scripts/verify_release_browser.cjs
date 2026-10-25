/* Read-only browser QA of the already prepared neural release; no user profile. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const release = path.resolve(root, process.env.RISKCASE_RELEASE || 'runs/release-demo-v1');
const base = process.env.RISKCASE_RELEASE_QA_URL || 'http://127.0.0.1:8768';
const target = new URL(base);
assert.equal(target.protocol, 'http:');
assert.equal(target.hostname, '127.0.0.1');
assert.equal(target.search, '');
assert.equal(target.hash, '');
assert.ok(release.startsWith(root + path.sep), 'Use a local project release');
const output = path.join(root, 'docs/assets');
fs.mkdirSync(output, {recursive: true});
const screenshot = path.join(output, 'neural-workbench.png');
const receiptFile = path.join(output, 'neural-workbench.qa.json');
const sha = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
const token = fs.readFileSync(path.join(release, 'service/auth.token'), 'ascii').trim();
const catalog = JSON.parse(fs.readFileSync(path.join(release, 'catalog.json'), 'utf8'));
const cases = JSON.parse(fs.readFileSync(path.join(release, 'cases.json'), 'utf8'));
assert.equal(cases.length, 4);
assert.equal(Object.keys(catalog).length, 4);
for (const row of cases) {
  const manifest = JSON.parse(fs.readFileSync(path.resolve(release, row.manifest), 'utf8'));
  assert.equal(manifest.data_kind, 'synthetic');
  assert.equal(manifest.alert.detector, 'neural_v2_ensemble');
  assert.equal(manifest.alert.score_kind, 'uncalibrated_ranking_sigmoid_not_probability');
  assert.equal(row.client_mode, 'KEYWORD_SCRIPT_NOT_REAL_LLM');
  assert.equal(row.state, 'awaiting_review');
  assert.equal(row.sql_verification.status, 'source_sql_verified');
}
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const executablePath = [process.env.RISKCASE_QA_BROWSER,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].filter(Boolean).find(p => fs.existsSync(p));
assert.ok(executablePath, 'Installed Edge is required; no browser downloads');
const database = path.join(release, 'service/jobs.sqlite');
const before = sha(database);
const receipt = {version: 1, started_at: new Date().toISOString(), target: base,
  release: path.relative(root, release).replaceAll('\\', '/'),
  isolation: 'new_headless_Edge_context_without_user_profile',
  source: 'IBM_TabFormer_synthetic', detector: 'neural_v2_ensemble',
  score_kind: 'uncalibrated_ranking_sigmoid_not_probability',
  real_LLM_requests: 0, live_enabled: false, expected_cases: 4,
  screenshot: 'docs/assets/neural-workbench.png',
  release_manifest_sha256: sha(path.join(release, 'manifest.json')),
  assertions: [], mutating_browser_requests: 0, external_browser_requests: 0,
  page_errors: [], console_errors: [], request_failures: []};
let browser;
const redact = value => String(value).split(token).join('[REDACTED]');

async function main() {
  browser = await playwright.chromium.launch({executablePath, headless: true,
    args: ['--disable-gpu', '--no-first-run', '--no-default-browser-check']});
  receipt.browser_version = browser.version();
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}, locale: 'zh-CN', serviceWorkers: 'block'});
  await context.route('**/*', async route => {
    const request = route.request();
    if (new URL(request.url()).origin !== target.origin) {
      receipt.external_browser_requests++;
      await route.abort();
    } else if (request.method() !== 'GET') {
      receipt.mutating_browser_requests++;
      await route.abort();
    } else await route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => receipt.page_errors.push(redact(error.message)));
  page.on('console', msg => { if (msg.type() === 'error') receipt.console_errors.push(redact(msg.text())); });
  page.on('requestfailed', request => receipt.request_failures.push({url: new URL(request.url()).pathname, error: redact(request.failure()?.errorText)}));
  const headers = {Authorization: 'Bearer ' + token};
  async function get(suffix) {
    const response = await context.request.get(base + suffix, {headers});
    assert.equal(response.status(), 200);
    return response.json();
  }
  const health = await get('/health');
  assert.equal(health.live_enabled, false);
  const registered = await get('/cases/details');
  assert.equal(registered.cases.length, 4);
  assert.deepEqual(registered.cases.map(row => row.case_id).sort(), Object.keys(catalog).sort());
  for (const row of registered.cases) {
    assert.notEqual(row.data_kind, 'self_authored_fixture');
    assert.match(row.selection, /Label-blind 2018 end-of-day top10/);
  }
  receipt.assertions.push('four_registered_neural_cases_with_label_blind_2018_synthetic_provenance');
  const initialJobs = await get('/jobs?limit=25');
  assert.equal(initialJobs.jobs.length, 4);
  for (const row of initialJobs.jobs) assert.equal(row.state, 'awaiting_review');
  await page.goto(base + '/', {waitUntil: 'networkidle'});
  await page.locator('#token').fill(token);
  await page.locator('#connect').click();
  await page.locator('#workspace').waitFor();
  await page.locator('#case option').nth(3).waitFor({state: 'attached'});
  assert.equal(await page.locator('#case option').count(), 4);
  await page.locator('#case').selectOption(cases[0].case_id);
  assert.match(await page.locator('#caseInfo').innerText(), /IBM合成数据案例，模型分数用于排序/);
  const mode = await page.locator('#mode').evaluate(select => [...select.options].map(option => ({value: option.value, disabled: option.disabled})));
  assert.equal(mode.find(option => option.value === 'live').disabled, true);
  await page.locator('#jobs .job').filter({hasText: cases[0].job_id.slice(-8)}).click();
  await page.locator('#state').filter({hasText: '报告等待审阅'}).waitFor();
  const report = await page.locator('#content pre').innerText();
  assert.match(report, /IBM合成数据的描述性调查/);
  assert.match(report, /模型排序分数不是已校准欺诈概率/);
  assert.match(report, /没有读取欺诈标签/);
  assert.match(report, /结论待人工审阅/);
  assert.equal(await page.getByRole('button', {name: '已核对，接受报告', exact: true}).count(), 1);
  receipt.assertions.push('existing_report_visible_with_synthetic_score_and_review_boundaries');
  receipt.assertions.push('live_model_mode_disabled_and_no_submit_confirm_or_accept_actions');
  await page.screenshot({path: screenshot, fullPage: true});
  receipt.screenshot_sha256 = sha(screenshot);
  const finalJobs = await get('/jobs?limit=25');
  assert.deepEqual(finalJobs, initialJobs);
  receipt.registered_cases = registered.cases.length;
  receipt.final_state_counts = {awaiting_review: finalJobs.jobs.length};
  const after = sha(database);
  assert.equal(after, before, 'Read-only QA must preserve the release service database');
  receipt.service_database_sha256_before = before;
  receipt.service_database_sha256_after = after;
  receipt.assertions.push('all_four_jobs_unchanged_awaiting_review_and_database_bytes_unchanged');
  assert.equal(receipt.mutating_browser_requests, 0);
  assert.equal(receipt.external_browser_requests, 0);
  assert.equal(receipt.page_errors.length, 0);
  assert.equal(receipt.console_errors.length, 0);
  assert.equal(receipt.request_failures.length, 0);
  receipt.status = 'passed';
  page.removeAllListeners('requestfailed');
  await context.close();
}

main().catch(error => { receipt.status = 'failed'; receipt.error = redact(error.message); process.exitCode = 1; })
  .finally(async () => {
    if (browser) await browser.close();
    receipt.finished_at = new Date().toISOString();
    receipt.script_sha256 = sha(__filename);
    const serialized = JSON.stringify(receipt, null, 2) + '\n';
    assert.ok(!serialized.includes(token), 'Credential must never be included in the receipt');
    fs.writeFileSync(receiptFile, serialized);
    console.log(serialized);
  });
