/* Shared navigation and accessible confirmation behavior; memory DOM, no browser/network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const documentListeners = new Map(), windowEvents = [];
let document;
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = new Map(); this.listeners = new Map(); this.style = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.textContent = ''; this.open = false; }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(item => item !== this); this.parentElement = null; }
  get isConnected() { return this === document.body || Boolean(this.parentElement?.isConnected); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  hasAttribute(name) { return this.attributes.has(name); }
  addEventListener(name, fn) { const entries = this.listeners.get(name) || []; entries.push(fn); this.listeners.set(name, entries); }
  dispatch(name) { const event = { type: name, target: this, preventDefault() {}, stopPropagation() {} }; for (const fn of this.listeners.get(name) || []) fn(event); }
  contains(element) { return this === element || this.children.some(child => child.contains(element)); }
  focus() { document.activeElement = this; }
  click() { this.dispatch('click'); }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatch('close'); }
  getClientRects() { return this.hidden || this.style.display === 'none' || (this.tagName === 'DIALOG' && !this.open) ? [] : [{}]; }
  querySelectorAll(selector) {
    const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    if (selector === '*') return all;
    if (selector.includes('button:not(:disabled)')) return all.filter(el => ['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && !el.disabled);
    if (selector === '[autofocus], [data-autofocus]') return all.filter(el => el.autofocus || el.hasAttribute('data-autofocus'));
    if (selector === '.modal-mask, dialog[open]') return all.filter(el => el.tagName === 'DIALOG' && el.open);
    if (selector.startsWith('.')) return all.filter(el => el.className === selector.slice(1));
    return all.filter(el => selector.split(',').map(tag => tag.trim().toUpperCase()).includes(el.tagName));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
document = {
  readyState: 'loading', activeElement: null,
  createElement: tag => new Element(tag),
  addEventListener(name, fn) { const entries = documentListeners.get(name) || []; entries.push(fn); documentListeners.set(name, entries); },
  querySelector: selector => document.body.querySelector(selector), querySelectorAll: selector => document.body.querySelectorAll(selector),
};
document.body = new Element('body');
const location = { hash: '' }, replacements = [];
const window = { dispatchEvent: event => windowEvents.push(event.type) };
const sandbox = { document, location, window, URLSearchParams, queueMicrotask,
  history: { replaceState(_state, _title, hash) { replacements.push(hash); location.hash = hash; } },
  HashChangeEvent: class { constructor(type) { this.type = type; } },
  MutationObserver: class { observe() {} }, setTimeout() {},
  fetch() { throw new Error('Network is forbidden'); },
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../assets/workspace-ui.js'), 'utf8'), sandbox);
const ui = window.WorkspaceUI, plain = value => JSON.parse(JSON.stringify(value)), flush = () => new Promise(setImmediate);
(async () => {
  assert.deepEqual(plain(ui.route('runs')), { page: 'runs', taskId: '', tab: '', leadId: '', promptId: '' });
  ui.navigate('reviews', { taskId: 12, tab: 'notes', leadId: 'xhs:评论 /?&=#', ignored: 'not-public' });
  assert.deepEqual(plain(ui.route()), { page: 'reviews', taskId: '12', tab: 'notes', leadId: 'xhs:评论 /?&=#', promptId: '' });
  assert.doesNotMatch(location.hash, /ignored|not-public/);
  ui.navigate('versions', { promptId: '83', taskId: '12' }, true);
  assert.deepEqual(plain(ui.route()), { page: 'versions', taskId: '12', tab: '', leadId: '', promptId: '83' });
  assert.equal(replacements.length, 1); assert.deepEqual(windowEvents, ['hashchange']);
  ui.navigate('versions', { promptId: '83', taskId: '12' }, true);
  assert.equal(replacements.length, 1, 'same route must not generate duplicate navigation');

  for (const fn of documentListeners.get('DOMContentLoaded')) fn();
  const opener = new Element('button'); document.body.append(opener); opener.focus();
  let settled = false;
  const confirmation = ui.confirm({ title: '单条确认', message: '<script>literal text</script>' }).then(answer => { settled = true; return answer; });
  await flush();
  const dialog = document.querySelector('dialog');
  assert.equal(settled, false, 'displaying confirmation cannot approve it');
  assert.equal(dialog.open, true); assert.equal(dialog.getAttribute('aria-modal'), 'true');
  assert.equal(dialog.querySelector('p').textContent, '<script>literal text</script>');
  assert.equal(document.activeElement.textContent, '取消', 'safe initial focus is cancel');
  for (const fn of documentListeners.get('keydown')) fn({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
  assert.equal(await confirmation, false); assert.equal(document.activeElement, opener);

  const first = ui.confirm('确认第一项'), second = ui.prompt({ title: '模板名称', value: '草稿' });
  await flush(); assert.equal(document.querySelectorAll('dialog').length, 1, 'confirmations serialize');
  document.querySelector('form').dispatch('submit'); assert.equal(await first, true);
  await flush();
  const input = document.querySelector('input'); input.value = '保留原样 & 中文';
  document.querySelector('form').dispatch('submit');
  assert.equal(await second, '保留原样 & 中文'); assert.equal(document.activeElement, opener);
  const cancelledPrompt = ui.prompt({ label: '填写说明' }); await flush();
  document.querySelector('dialog').dispatch('cancel'); assert.equal(await cancelledPrompt, null);
  let multilineSettled = false;
  const multiline = ui.prompt({ title: '补充漏识别反馈', multiline: true, value: '第一行原文\n第二行原文' }).then(value => { multilineSettled = true; return value; });
  await flush();
  const textarea = document.querySelector('textarea'); assert.ok(textarea); assert.equal(textarea.rows, 5);
  assert.equal(textarea.value, '第一行原文\n第二行原文'); assert.equal(document.activeElement, textarea);
  let enterPrevented = false;
  for (const fn of documentListeners.get('keydown')) fn({ key: 'Enter', target: textarea, preventDefault() { enterPrevented = true; }, stopPropagation() {} });
  await flush(); assert.equal(enterPrevented, false, 'shared keyboard handler leaves textarea Enter to native editing');
  assert.equal(multilineSettled, false, 'Enter inside a multiline field does not authorize submission');
  textarea.value = '第一行 <原文>\n\n最后一行 & 备注'; document.querySelector('form').dispatch('submit');
  assert.equal(await multiline, '第一行 <原文>\n\n最后一行 & 备注'); assert.equal(document.activeElement, opener);
  const cancelledMultiline = ui.prompt({ multiline: true }); await flush();
  document.querySelector('textarea').value = '尚未提交的草稿\n保持取消';
  for (const fn of documentListeners.get('keydown')) fn({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
  assert.equal(await cancelledMultiline, null); assert.equal(document.activeElement, opener);
  assert.equal(document.querySelectorAll('dialog').length, 0);
  console.log('PASS: encoded context routes, explicit confirmation, multiline values/native Enter, Escape cancellation, serialized prompts and focus restoration.');
})().catch(error => { console.error(error); process.exitCode = 1; });
