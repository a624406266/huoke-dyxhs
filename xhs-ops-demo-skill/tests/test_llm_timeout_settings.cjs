/* Isolated Harness UI checks: no dependencies, HTTP server, account, or network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.resolve(__dirname, '../assets/harness.html'), 'utf8');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { value: '', checked: false, disabled: false, textContent: '', style: {} });
  return elements.get(id);
}
const alerts = [], posts = [];
const context = vm.createContext({
  document: { getElementById: element },
  localStorage: { getItem() { return null; } },
  window: { addEventListener() {} },
  alert: message => alerts.push(message),
  mockSettings: {},
  mockPost: async (url, body) => posts.push({ url, body: JSON.parse(JSON.stringify(body)) }),
  // Fail closed if production fetch slips through the explicit UI API stubs.
  fetch() { throw new Error('Network calls are forbidden in this test'); },
});
require('./ui-libraries.cjs').loadUiLibraries(context, path.resolve(__dirname, '../assets'));
for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInContext(script[1], context);
const run = source => vm.runInContext(source, context);
run('adminReady = true; get = async () => mockSettings; post = mockPost;');
const fields = [
  ['connect_timeout_s', 'settingConnectTimeout', 30],
  ['read_timeout_s', 'settingReadTimeout', 60],
  ['total_timeout_s', 'settingTotalTimeout', 300],
];
const plain = value => JSON.parse(JSON.stringify(value));
(async () => {
  for (const [, id, value] of fields) {
    assert.match(html, new RegExp(`<input id="${id}"[^>]*min="1"[^>]*max="86400"[^>]*step="1"[^>]*value="${value}"[^>]*required`));
  }
  context.mockSettings = { llm: { api_base: 'https://model.invalid/v1', model: 'chosen-model', has_api_key: true } };
  await run('readSettingsIntoForm()');
  for (const [, id, value] of fields) assert.equal(Number(element(id).value), value);
  assert.equal(element('settingApiKey').value, '');
  await run("post('/api/settings', readSettingsForm())");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/api/settings');
  assert.deepEqual(posts[0].body.llm, {
    api_base: 'https://model.invalid/v1', model: 'chosen-model', temperature: 0.6,
    connect_timeout_s: 30, read_timeout_s: 60, total_timeout_s: 300,
  });
  assert.equal(posts[0].body.platforms.enabled[0], 'xhs');

  context.mockSettings = { llm: { connect_timeout_s: 18, read_timeout_s: 123, total_timeout_s: 990 } };
  await run('readSettingsIntoForm()');
  assert.deepEqual(plain(run('readLlmTimeoutSettings()')), { connect_timeout_s: 18, read_timeout_s: 123, total_timeout_s: 990 });
  element('settingConnectTimeout').value = '1';
  element('settingReadTimeout').value = '86400';
  element('settingTotalTimeout').value = '300';
  await run("post('/api/settings', readSettingsForm())");
  assert.equal(posts.length, 2);
  assert.equal(posts[1].body.llm.connect_timeout_s, 1);
  assert.equal(posts[1].body.llm.read_timeout_s, 86400);

  for (const [, id] of fields) {
    for (const invalid of ['', ' ', '0', '-1', '1.5', 'NaN', 'Infinity', '86401', 'true']) {
      for (const [, otherId, value] of fields) element(otherId).value = String(value);
      element(id).value = invalid;
      const before = posts.length;
      assert.throws(() => run('readSettingsForm()'), /1–86400/);
      assert.equal(posts.length, before, `${id}=${invalid} must not reach the API`);
        }
  }
  assert.match(html, /相邻数据的最长间隔/);
  assert.match(html, /不是总生成时间/);
  assert.match(html, /不在重试时重置/);
  console.log('PASS: model timeout defaults, load/save payload, existing model and key handling, bounds, invalid-value rejection, and form validation.');
})().catch(error => { console.error(error); process.exitCode = 1; });
