/* Read-only live UI check; isolated profile; every non-GET request is blocked. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.WORKBENCH_BASE_URL || 'http://127.0.0.1:8765';
assert.equal(new URL(base).hostname, '127.0.0.1', 'This test is local-only');
let browser;
(async () => {
  for (const file of ['index.html', 'workbench.js', 'excel-export.js', 'harness.html', 'harness-evaluations.js']) {
    const response = await fetch(`${base}/${file}`); assert.equal(response.status, 200); const source = await response.text();
    assert.ok(source.length > 100); if (file.endsWith('.js')) assert.match(response.headers.get('content-type'), /javascript/);
    console.log('GET', file, response.status, response.headers.get('content-type'));
  }
  const gate = await fetch(`${base}/api/auth/me`); const gateBody = await gate.json(); assert.ok(gate.status === 401 || (gate.status === 200 && !gateBody.data?.user));
  const protectedConfig = await fetch(`${base}/api/templates`); assert.equal(protectedConfig.status, 401);
  browser = await chromium.launch({ headless: true, chromiumSandbox: true, executablePath: process.env.WORKBENCH_CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], writes = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(); requests.push(request.url());
    if (request.method() !== 'GET') { writes.push(request.method() + ' ' + request.url()); return route.abort(); }
    if (new URL(request.url()).origin !== base) return route.abort();
    return route.continue();
  });
  await page.goto(`${base}/index.html`); await page.locator('#workbenchGate').waitFor({ state: 'visible' });
  await page.waitForTimeout(10000);
  assert.equal(requests.filter(url => url.endsWith('/api/auth/me')).length, 1);
  assert.equal(requests.filter(url => url.includes('/api/tasks/status')).length, 0);
  await page.goto(`${base}/harness.html`);
  await page.locator('#adminGate').waitFor({ state: 'visible' });
  assert.deepEqual(writes, []); assert.deepEqual(errors, []);
  console.log('PASS: live public assets, 401 login gate, 10-second no-task/no-retry loop, Harness login/setup view; no write requests or account creation.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
