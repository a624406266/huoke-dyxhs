/* Shared UI primitives. Business confirmation and permissions remain in callers. */
(function () {
  'use strict';
  const dialogStack = [];
  const openers = new WeakMap();
  const focusable = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
  function visible(el) { return !!el && !el.hidden && !!el.getClientRects().length; }
  function focusables(el) { return [...el.querySelectorAll(focusable)].filter(visible); }
  function registerDialog(el) {
    if (dialogStack.includes(el)) return;
    openers.set(el, document.activeElement);
    dialogStack.push(el);
    el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true');
    if (!el.hasAttribute('aria-label') && !el.hasAttribute('aria-labelledby')) {
      const title = el.querySelector('h1,h2,h3');
      if (title) el.setAttribute('aria-label', title.textContent.trim());
    }
    queueMicrotask(() => {
      if (!visible(el) || el.contains(document.activeElement)) return;
      const target = el.querySelector('[autofocus], [data-autofocus]') || focusables(el)[0] || el;
      if (target === el && !el.hasAttribute('tabindex')) el.tabIndex = -1;
      target.focus();
    });
  }
  function unregisterDialog(el) {
    const index = dialogStack.indexOf(el); if (index < 0) return;
    dialogStack.splice(index, 1);
    const opener = openers.get(el); openers.delete(el);
    if (opener?.isConnected && visible(opener)) opener.focus({ preventScroll: true });
  }
  function showDialog(el) {
    if (!el) return;
    const opener = document.activeElement;
    if (el.tagName === 'DIALOG') { if (!el.open) el.showModal(); }
    else { el.hidden = false; el.style.display = 'flex'; }
    registerDialog(el); openers.set(el, opener);
  }
  function closeDialog(el) {
    if (!el) return;
    if (el.tagName === 'DIALOG') { if (el.open) el.close(); }
    else el.style.display = 'none';
    unregisterDialog(el);
  }
  function closeTopDialog(el) {
    // Use the existing close handler to preserve outreach/login cleanup.
    const close = [...el.querySelectorAll('button')].find(button =>
      !button.disabled && (/^(关闭|取消|返回)$/.test(button.textContent.trim()) || button.getAttribute('aria-label') === '关闭'));
    if (close) close.click(); else closeDialog(el);
  }
  function notify(message, tone = 'info') {
    let region = document.querySelector('.workspace-toast-region');
    if (!region) {
      region = document.createElement('div'); region.className = 'workspace-toast-region';
      region.setAttribute('role', 'status'); region.setAttribute('aria-live', 'polite'); document.body.append(region);
    }
    const toast = document.createElement('div'); toast.className = 'workspace-toast'; toast.dataset.tone = tone;
    const text = document.createElement('span'); text.textContent = window.DisplayText ? DisplayText.message(message) : String(message || '');
    const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.textContent = '×'; dismiss.setAttribute('aria-label', '关闭提示');
    dismiss.addEventListener('click', () => toast.remove()); toast.append(text, dismiss); region.append(toast);
    setTimeout(() => toast.remove(), tone === 'error' ? 10000 : 6000);
  }
  let promptQueue = Promise.resolve();
  function ask(options, inputMode) {
    const job = () => new Promise(resolve => {
      const settings = typeof options === 'string' ? { message: options } : options || {};
      const dialog = document.createElement('dialog'); dialog.className = 'workspace-dialog';
      const form = document.createElement('form'); form.method = 'dialog';
      const title = document.createElement('h2'); title.textContent = settings.title || (inputMode ? '填写信息' : '确认操作');
      form.append(title);
      if (settings.message) { const message = document.createElement('p'); message.textContent = settings.message; form.append(message); }
      let input;
      if (inputMode) {
        const label = document.createElement('label'); label.textContent = settings.label || '内容';
        input = document.createElement(settings.multiline ? 'textarea' : 'input');
        if (settings.multiline) input.rows = 5; else input.type = 'text';
        input.value = settings.value || ''; input.autofocus = true;
        label.append(input); form.append(label);
      }
      const actions = document.createElement('div'); actions.className = 'workspace-dialog-actions';
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = settings.cancelText || '取消';
      if (!inputMode) cancel.autofocus = true;
      const confirm = document.createElement('button'); confirm.type = 'submit'; confirm.className = settings.danger ? 'danger' : 'primary'; confirm.textContent = settings.confirmText || '确认';
      actions.append(cancel, confirm); form.append(actions); dialog.append(form); document.body.append(dialog);
      let answer = inputMode ? null : false;
      cancel.addEventListener('click', () => closeDialog(dialog));
      form.addEventListener('submit', event => { event.preventDefault(); answer = inputMode ? input.value : true; closeDialog(dialog); });
      dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog); });
      dialog.addEventListener('close', () => { unregisterDialog(dialog); dialog.remove(); resolve(answer); }, { once: true });
      showDialog(dialog);
    });
    const result = promptQueue.then(job, job); promptQueue = result.then(() => {}, () => {}); return result;
  }
  function route(defaultPage = 'tasks') {
    const hash = location.hash.replace(/^#\/?/, '');
    const [path, search = ''] = hash.split('?'); const params = new URLSearchParams(search);
    return { page: path || defaultPage, taskId: params.get('task') || '', tab: params.get('tab') || '', leadId: params.get('lead') || '', promptId: params.get('prompt') || '' };
  }
  function navigate(page, params = {}, replace = false) {
    const query = new URLSearchParams();
    for (const [key, field] of Object.entries({ taskId: 'task', tab: 'tab', leadId: 'lead', promptId: 'prompt' })) {
      if (params[key] !== undefined && params[key] !== null && String(params[key]) !== '') query.set(field, params[key]);
    }
    const hash = '#/' + encodeURIComponent(page || 'tasks') + (query.size ? '?' + query.toString() : '');
    if (location.hash === hash) return;
    if (replace) { history.replaceState(null, '', hash); window.dispatchEvent(new HashChangeEvent('hashchange')); }
    else location.hash = hash;
  }
  const shapes = {
    tasks: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    review: '<path d="M9 11l2 2 5-5"/><path d="M20 12a8 8 0 1 1-5-7.4"/>',
    outreach: '<path d="M21 3 9 15M21 3l-7 18-5-6-6-5 18-7Z"/>',
    templates: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M14 17.5h7M17.5 14v7"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    settings: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor" stroke="none"/><circle cx="15" cy="17" r="3" fill="currentColor" stroke="none"/>',
    versions: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M6 7v10M18 7v2a6 6 0 0 1-6 6H6"/>',
    feedback: '<path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8M8 12h5"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    logo: '<path d="m4 16 5-8 4 5 7-10M4 21h16"/><circle cx="20" cy="3" r="1"/>'
  };
  function icon(name) { return '<svg class="workspace-icon" viewBox="0 0 24 24" aria-hidden="true">' + (shapes[name] || shapes.tasks) + '</svg>'; }
  let buttonsWired = false;
  function wireButtonFeedback() {
    if (buttonsWired) return;
    buttonsWired = true;
    document.addEventListener('pointerdown', event => {
      const button = event.target?.closest?.('button');
      if (button && !button.disabled) button.classList.add('is-pressing');
    });
    for (const type of ['pointerup', 'pointercancel', 'pointerleave']) document.addEventListener(type, () => {
      document.querySelectorAll('button.is-pressing').forEach(button => button.classList.remove('is-pressing'));
    });
  }
  function boot() {
    document.addEventListener('keydown', event => {
      const el = dialogStack.filter(visible).at(-1); if (!el) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeTopDialog(el); return; }
      if (event.key !== 'Tab') return;
      const items = focusables(el), first = items[0], last = items.at(-1);
      if (!first) { event.preventDefault(); el.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !el.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }, true);
    document.addEventListener('close', event => { if (event.target.tagName === 'DIALOG') unregisterDialog(event.target); }, true);
    let scheduled = false;
    const scan = () => {
      scheduled = false;
      for (const el of [...dialogStack]) if (!visible(el)) unregisterDialog(el);
      for (const el of document.querySelectorAll('.modal-mask, dialog[open]')) if (visible(el)) registerDialog(el);
    };
    new MutationObserver(() => {
      if (scheduled) return; scheduled = true; queueMicrotask(scan);
    }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['style', 'class', 'open', 'hidden'] });
    scan();
  }
  window.WorkspaceUI = Object.freeze({ notify, confirm: options => ask(options, false), prompt: options => ask(options, true), showDialog, closeDialog, route, navigate, icon, wireButtonFeedback });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
})();
