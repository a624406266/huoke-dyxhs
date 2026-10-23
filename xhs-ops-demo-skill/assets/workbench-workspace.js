/* View composition only. Collection, review and outreach retain their original API gates. */
'use strict';
(function () {
  const state = { page: 'tasks', step: 1, tasks: [], selectedLead: '', query: '', review: 'pending', pendingResult: null, signature: '', lastSync: null, routing: false, routePending: false, ready: false };
  const pageNames = { tasks: '任务', reviews: '线索审核', outreach: '触达记录', templates: '任务模板', new: '新建任务' };
  const byId = id => document.getElementById(id);
  const icon = name => window.WorkspaceUI?.icon(name) || '';
  const compact = () => window.innerWidth < 1280;
  const notify = (message, tone = 'info') => window.WorkspaceUI?.notify(systemText(message), tone);
  const route = () => window.WorkspaceUI?.route('tasks') || { page: 'tasks' };
  const params = () => ({ ...(currentTaskId ? { taskId: currentTaskId } : {}), ...(state.selectedLead ? { leadId: state.selectedLead } : {}), tab: document.querySelector('[data-result-tab].active')?.dataset.resultTab || 'leads' });
  function navigate(page, values = {}, replace = false) {
    if (window.WorkspaceUI) window.WorkspaceUI.navigate(page, values, replace);
    else location.hash = `/${page}?${new URLSearchParams(values)}`;
    applyRoute();
  }
  function make(tag, className = '', html = '') { const element = document.createElement(tag); element.className = className; element.innerHTML = html; return element; }
  function setNav(open, restoreFocus = true) {
    const sidebar = byId('wbSidebar'), button = document.querySelector('.wb-menu-button');
    if (!sidebar || !button) return;
    open = Boolean(open && window.innerWidth < 1024);
    const wasOpen = sidebar.classList.contains('is-open');
    sidebar.classList.toggle('is-open', open);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-label', open ? '关闭导航' : '展开导航');
    const backdrop = byId('wbNavBackdrop'); if (backdrop) backdrop.hidden = !open;
    const frame = document.querySelector('.wb-frame'); if (frame) frame.inert = open;
    if (open && !wasOpen) sidebar.querySelector('a.active, a')?.focus();
    else if (wasOpen && restoreFocus) button.focus();
  }
  function initialize() {
    if (state.ready || !byId('salesMain')) return;
    state.ready = true;
    if (!location.hash) {
      try { const saved = JSON.parse(localStorage.getItem('keywordWorkbenchView') || 'null'); if (saved && pageNames[saved.page]) window.WorkspaceUI?.navigate(saved.page, { taskId: saved.taskId, tab: saved.tab, leadId: saved.leadId }, true); } catch {}
    }
    document.body.classList.add('wb-workspace');
    document.body.classList.toggle('wb-signed-out', !authenticated);
    const shell = make('div', 'workspace-shell wb-shell');
    const sidebar = make('aside', 'workspace-sidebar wb-sidebar', `<a class="workspace-brand wb-brand" href="#/tasks"><span class="wb-brand-mark">${icon('logo')}</span><span>获客工作台<small>本机工作空间</small></span></a><button type="button" class="wb-new-button" onclick="WorkbenchWorkspace.newTask()">${icon('plus')} 新建任务</button><nav aria-label="工作台导航">${[['tasks','tasks'],['reviews','review'],['outreach','outreach'],['templates','templates']].map(([key, iconName]) => `<a href="#/${key}" data-wb-nav="${key}"><span aria-hidden="true">${icon(iconName)}</span>${pageNames[key]}</a>`).join('')}</nav><div class="wb-sidebar-footer"><a id="wbHarnessLink" href="harness.html">维护后台 <span aria-hidden="true">↗</span></a><p>本地数据 · 人工确认触达</p></div>`);
    sidebar.id = 'wbSidebar';
    const frame = make('div', 'workspace-main wb-frame');
    const topbar = make('div', 'workspace-topbar wb-topbar', '<div><button class="wb-menu-button" type="button" aria-label="展开导航" aria-expanded="false" onclick="WorkbenchWorkspace.toggleNav()">☰</button><span class="wb-breadcrumb">工作空间 <span>/</span> <strong id="wbPageTitle">任务</strong></span></div><div id="wbSessionActions" class="wb-session-actions"></div>');
    const content = make('div', 'workspace-content wb-content');
    const originalHeader = document.querySelector('body > header');
    document.body.insertBefore(shell, document.body.firstChild); shell.append(sidebar, frame); frame.append(topbar, content);
    const navBackdrop = make('button', 'wb-nav-backdrop'); navBackdrop.id = 'wbNavBackdrop'; navBackdrop.type = 'button'; navBackdrop.hidden = true; navBackdrop.setAttribute('aria-label', '关闭导航'); navBackdrop.addEventListener('click', () => setNav(false)); shell.append(navBackdrop);
    byId('wbSessionActions')?.append(byId('salesNav'));
    content.append(byId('workbenchGate'), byId('salesMain'));
    originalHeader.hidden = true;
    const main = byId('salesMain');
    const status = byId('status'); status.classList.add('wb-global-status');
    const connection = make('div', 'wb-connection', '<span id="wbConnectionText"></span><button type="button" class="btn-secondary" onclick="WorkbenchWorkspace.refreshConnection()">重试连接</button>'); connection.id = 'wbConnection'; connection.hidden = true;
    main.prepend(connection);
    const pages = {};
    Object.keys(pageNames).forEach(name => { const page = make('section', 'wb-page'); page.id = `wb-page-${name}`; page.hidden = true; pages[name] = page; main.append(page); });
    pages.tasks.innerHTML = '<div class="wb-page-heading"><div><h1>研究任务</h1><p>查看采集进展、候选结果与待处理事项。</p></div><button type="button" onclick="WorkbenchWorkspace.newTask()">＋ 新建任务</button></div><div id="wbTaskMetrics" class="wb-metrics"></div><section class="workspace-card wb-task-card"><div class="wb-section-toolbar"><h2>最近任务</h2><div class="wb-inline-actions"><input id="wbTaskSearch" aria-label="搜索任务" placeholder="搜索关键词或任务编号" /><button type="button" class="btn-secondary" onclick="loadTaskHistory()">刷新</button></div></div><div id="wbTaskTable"></div></section>';
    pages.new.append(byId('wbConfiguration'), byId('planPanel'));
    pages.reviews.append(document.querySelector('.results'));
    pages.reviews.prepend(make('div', 'wb-review-heading', '<div><h1>线索审核</h1><p id="wbReviewContext">选择任务，逐条对照原文与模型判断。</p></div><a href="#/tasks" class="wb-text-link">切换任务</a>'));
    const outreach = byId('outreachHistoryPanel'); outreach.classList.remove('history-bottom'); pages.outreach.append(make('div', 'wb-page-heading', '<div><h1>触达记录</h1><p>查看单条动作与平台回执，按动作编号核实结果。</p></div>'), outreach); outreach.querySelector('h2').textContent = '最近动作';
    pages.templates.innerHTML = '<div class="wb-page-heading"><div><h1>任务模板</h1><p>复用研究目标与采集预算，启动前仍会生成并确认新计划。</p></div><button type="button" onclick="WorkbenchWorkspace.newTemplate()">新建模板</button></div><div id="wbTemplateCards" class="wb-template-grid"></div>';
    byId('taskHistory').hidden = true; byId('taskHistory').classList.add('wb-legacy-history'); main.append(byId('taskHistory'));
    buildWizard();
    document.querySelector('.wb-menu-button').innerHTML = icon('menu');
    document.querySelector('.wb-menu-button').setAttribute('aria-controls', 'wbSidebar');
    document.querySelectorAll('.wb-new-button,.wb-page-heading>button').forEach(button => button.classList.add('primary'));
    byId('wbTaskSearch').addEventListener('input', renderTaskTable);
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopTaskPolling(); else if (authenticated && (currentTaskSnapshot?.isRunning || currentTaskSnapshot?.isEvolving) && currentTaskId) startPolling(currentTaskId); });
    document.addEventListener('focusout', () => setTimeout(flushPendingResults, 0));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && document.querySelector('.wb-sidebar').classList.contains('is-open')) { event.preventDefault(); setNav(false); return; }
      if (event.key === 'Escape' && !event.defaultPrevented && state.selectedLead) { closeDetail(); return; }
      const tab = event.target.closest?.('[data-result-tab]');
      if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = [...document.querySelectorAll('[data-result-tab]')], index = tabs.indexOf(tab);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); switchResultTab(tabs[next].dataset.resultTab); tabs[next].focus();
    });
    window.addEventListener('hashchange', applyRoute);
    window.addEventListener('online', () => { if (authenticated && (currentTaskSnapshot?.isRunning || currentTaskSnapshot?.isEvolving)) startPolling(currentTaskId); });
    window.addEventListener('resize', () => { if (window.innerWidth >= 1024) setNav(false, false); selectLead(state.selectedLead, false); });
    window.addEventListener('scroll', layoutDetail, { passive: true });
    ['xhsLoginModal', 'douyinLoginModal'].forEach(id => { const modal = byId(id); modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); const heading = modal.querySelector('h2'); heading.id = `${id}-title`; modal.setAttribute('aria-labelledby', heading.id); modal.querySelector('.icon-btn').setAttribute('aria-label', '关闭'); });
    const reviewHistory = make('div', 'modal-mask', '<div class="modal"><div class="modal-head"><h2 id="wbReviewHistoryTitle">人工审核记录</h2><button type="button" class="icon-btn" aria-label="关闭" onclick="WorkspaceUI.closeDialog(document.getElementById(\'wbReviewHistoryDialog\'))">×</button></div><div id="wbReviewHistoryBody" class="modal-body"></div></div>');
    reviewHistory.id = 'wbReviewHistoryDialog'; reviewHistory.setAttribute('role', 'dialog'); reviewHistory.setAttribute('aria-modal', 'true'); reviewHistory.setAttribute('aria-labelledby', 'wbReviewHistoryTitle'); document.body.append(reviewHistory);
    renderTaskTable(); templatesUpdated(); applyRoute();
  }
  function buildWizard() {
    const config = byId('wbConfiguration'), grid = config.querySelector('.config-grid');
    const [goals, options] = [...grid.children];
    const steps = make('ol', 'wb-stepper', '<li data-wb-step="1"><button type="button" onclick="WorkbenchWorkspace.step(1)"><span>1</span>目标范围</button></li><li data-wb-step="2"><button type="button" onclick="WorkbenchWorkspace.step(2)"><span>2</span>搜索计划</button></li><li data-wb-step="3"><button type="button" onclick="WorkbenchWorkspace.step(3)"><span>3</span>确认执行</button></li>');
    config.prepend(make('div', 'wb-wizard-heading', '<h1>新建研究任务</h1><p>定义目标与范围，检查搜索计划，确认后才开始采集。</p>'), steps);
    config.querySelector('.panel-head').hidden = true;
    goals.classList.add('wb-step-content'); goals.id = 'wbStepGoals'; options.classList.add('wb-step-content'); options.id = 'wbStepBudget';
    const templates = make('div', 'wb-template-picker', '<label for="taskTemplate">从模板开始</label>');
    templates.append(byId('taskTemplate')); goals.prepend(templates);
    [...options.querySelectorAll('label[for="taskTemplate"]')].forEach(label => label.remove());
    options.prepend(make('h2', 'wb-budget-title', '采集范围'));
    const advanced = make('details', 'wb-advanced', '<summary>采集预算与高级选项 <span>数量、阈值、时效、排序与扫描节奏</span></summary><div class="wb-advanced-content"></div>');
    options.append(advanced);
    const advancedBody = advanced.querySelector('div');
    const moveField = id => { const field = byId(id), row = field.closest('.row'); if (row && row.parentElement === options) { advancedBody.append(row); return; } const label = options.querySelector(`label[for="${id}"]`); if (label) advancedBody.append(label); advancedBody.append(field); };
    ['count', 'maxKeywords', 'maxCommentsPerNote', 'sort', 'metric', 'delayMin', 'readDetail', 'scanLeads', 'batchMode'].forEach(id => {
      const field = byId(id); if (advancedBody.contains(field)) return;
      if (id === 'readDetail' || id === 'scanLeads') advancedBody.append(field.closest('label'));
      else if (id === 'batchMode') advancedBody.append(field.closest('.hint'));
      else moveField(id);
    });
    [...options.querySelectorAll(':scope > label')].filter(label => label.textContent.includes('采集延迟')).forEach(label => advancedBody.insertBefore(label, byId('delayMin').closest('.row')));
    [...options.querySelectorAll(':scope > .field-help')].forEach(help => advancedBody.append(help));
    const budgetSummary = make('p', 'wb-budget-summary'); budgetSummary.id = 'wbBudgetSummary';
    const refreshBudget = () => { budgetSummary.textContent = `当前预算：最多 ${byId('count').value} 篇笔记 · ${byId('maxCommentsTotal').value} 条评论 · ${byId('maxKeywords').value} 个搜索词`; };
    ['count', 'maxCommentsTotal', 'maxKeywords'].forEach(id => byId(id).addEventListener('input', refreshBudget)); refreshBudget();
    const commandRow = byId('searchBtn').parentElement; options.insertBefore(advanced, commandRow);
    advanced.before(budgetSummary);
    commandRow.id = 'wbStep1Actions'; commandRow.classList.add('wb-wizard-actions'); config.append(commandRow); commandRow.append(byId('searchBtn')); byId('searchBtn').textContent = '生成搜索计划 →';
    for (const [id, label] of Object.entries({ delayMin: '最短采集间隔（秒）', delayMax: '最长采集间隔（秒）', intervalMin: '最短轮间隔（分钟）', intervalMax: '最长轮间隔（分钟）' })) byId(id).setAttribute('aria-label', label);
    config.querySelector(':scope > .hint').classList.add('wb-boundary-note');
    const plan = byId('planPanel'); plan.classList.add('wb-plan-preview');
    plan.querySelector('h2').textContent = '编辑搜索计划';
    const editActions = make('div', 'wb-wizard-actions', '<button type="button" class="btn-secondary" onclick="WorkbenchWorkspace.step(1)">← 调整目标与范围</button><button id="wbReviewPlanBtn" type="button" class="primary" onclick="WorkbenchWorkspace.step(3)">下一步：确认执行 →</button>'); plan.append(editActions);
    const confirmation = make('section', 'card panel wb-plan-preview', '<h2>确认本次采集</h2><p class="small">检查完整计划与采集范围。确认后将固定使用以下内容。</p><div id="wbConfirmationSnapshot"></div><div class="wb-wizard-actions"><button type="button" class="btn-secondary" onclick="WorkbenchWorkspace.step(2)">← 返回编辑计划</button></div>');
    confirmation.id = 'wbConfirmation'; confirmation.hidden = true; plan.after(confirmation);
    confirmation.querySelector('.wb-wizard-actions').append(byId('confirmSearchBtn')); byId('confirmSearchBtn').textContent = '确认计划并开始采集';
    step(1, false);
  }
  function step(number, validate = true) {
    if (number > 1 && !activeSearchPlan) { notify('请先在目标范围中生成搜索计划。'); return; }
    if (number === 3) {
      try {
        const p = payload(), words = splitKeywords(byId('planKeywords').value);
        if (!words.length || words.length > p.maxKeywords) throw new Error(`请保留 1–${p.maxKeywords} 个实际执行关键词。`);
        if (JSON.stringify(p.seedKeywords) !== activeSearchPlan.originalSeeds || p.offeringId !== activeSearchPlan.originalOffering) throw new Error('目标或种子词已变化，请重新生成计划。');
        const goal = byId('planGoal').value.trim() || p.searchGoal;
        byId('wbConfirmationSnapshot').innerHTML = `<div class="wb-confirm-summary"><span>服务模式</span><strong>${esc(offeringLabel(p.offeringId))}</strong><span>研究目标</span><strong>${esc(goal)}</strong><span>执行关键词</span><strong>${words.map(word => `<span class="pill">${esc(word)}</span>`).join(' ')}</strong><span>排除条件</span><strong>${esc(byId('planExclusions').value || '无')}</strong><span>平台与范围</span><strong>${esc(p.platforms.map(platformLabel).join('、'))} · 每词 ${p.maxPagesPerKeyword} 页 · 最多 ${p.maxNotes} 篇笔记</strong><span>评论预算</span><strong>每篇 ${p.maxCommentsPerNote} 条 · 全任务 ${p.maxCommentsTotal} 条</strong><span>采集节奏</span><strong>${p.delayMin}–${p.delayMax} 秒 · ${byId('batchMode').checked ? '后台多轮扫描' : '单轮采集'}</strong><span>排序与时效</span><strong>${esc(byId('sort').selectedOptions[0].textContent)} · ${esc(byId('timeFilter').selectedOptions[0].textContent)}</strong><span>计划来源</span><strong>${esc(byId('planOrigin').textContent)}</strong></div><div class="hint">${esc(byId('planWarnings').textContent)}<br>本次只执行读取和分析。关注、评论、回复及私信仍需另外逐条确认。</div>`;
      } catch (error) { notify(error.message, 'error'); return; }
    }
    state.step = number;
    byId('wbConfiguration').querySelector('.config-grid').hidden = number !== 1;
    byId('wbStep1Actions').hidden = number !== 1;
    byId('wbConfiguration').querySelector('.wb-boundary-note').hidden = number !== 1;
    byId('planPanel').style.display = number === 2 ? 'block' : 'none';
    byId('wbConfirmation').hidden = number !== 3;
    document.querySelectorAll('[data-wb-step]').forEach(item => { item.classList.toggle('active', Number(item.dataset.wbStep) === number); item.classList.toggle('complete', Number(item.dataset.wbStep) < number); const button = item.querySelector('button'); button.disabled = Number(item.dataset.wbStep) > 1 && !activeSearchPlan; button.setAttribute('aria-current', Number(item.dataset.wbStep) === number ? 'step' : 'false'); });
  }
  async function applyRoute() {
    if (!state.ready) return;
    if (state.routing) { state.routePending = true; return; }
    const current = route();
    const requestedHash = location.hash;
    const aliases = { review: 'reviews', leads: 'reviews', task: 'reviews' };
    const page = aliases[current.page] || (pageNames[current.page] ? current.page : 'tasks');
    state.page = page;
    localStorage.setItem('keywordWorkbenchView', JSON.stringify({ page, taskId: current.taskId || currentTaskId || '', tab: current.tab || 'leads', leadId: current.leadId || '' }));
    document.querySelectorAll('.wb-page').forEach(element => { element.hidden = element.id !== `wb-page-${page}`; });
    document.querySelectorAll('[data-wb-nav]').forEach(link => { const active = link.dataset.wbNav === page; link.classList.toggle('active', active); if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
    byId('wbPageTitle').textContent = pageNames[page];
    setNav(false, false);
    state.selectedLead = current.leadId || '';
    if (authenticated && current.taskId && /^\d+$/.test(String(current.taskId)) && String(currentTaskId) !== String(current.taskId)) {
      state.routing = true; try { await viewTask(current.taskId); } finally { state.routing = false; }
      if (state.routePending || location.hash !== requestedHash) { state.routePending = false; return applyRoute(); }
    }
    // A task switch clears the old selection before reading. Restore only the
    // still-current deep link after the requested task has finished loading.
    state.selectedLead = current.leadId || '';
    if (page === 'reviews' && state.selectedLead && cachedData?.leads?.leads?.some(lead => String(lead.lead_id) === String(state.selectedLead))) {
      const included = filteredLeadEntries().some(({ lead }) => String(lead.lead_id) === String(state.selectedLead));
      const adjusted = [];
      if (!visibleEntries().some(({ lead }) => String(lead.lead_id) === String(state.selectedLead))) {
        if (state.review !== 'all') adjusted.push('全部审核状态');
        if (state.query) adjusted.push('清除文本搜索');
        state.review = 'all'; state.query = '';
        if (byId('wbReviewFilter')) byId('wbReviewFilter').value = 'all';
        if (byId('wbLeadSearch')) byId('wbLeadSearch').value = '';
      }
      if (!included) {
        adjusted.push('全部需求分类');
        leadViewFilter = 'all'; state.signature = '';
        const previousRouting = state.routing; state.routing = true; state.restoringRoute = true;
        try { render(cachedData); } finally { state.restoringRoute = false; state.routing = previousRouting; }
      } else renderLeadQueue();
      state.selectedLead = current.leadId;
      if (adjusted.length) notify(`为打开链接中的候选，显示范围已调整：${adjusted.join('、')}。`);
    } else if (page === 'reviews' && state.selectedLead && cachedData) state.selectedLead = '';
    if (current.tab && ['leads', 'report', 'notes', 'logs'].includes(current.tab)) { const previousRouting = state.routing; state.routing = true; try { switchResultTab(current.tab); } finally { state.routing = previousRouting; } }
    if (page === 'reviews') selectLead(state.selectedLead, false);
    else { const backdrop = byId('wbDetailBackdrop'); if (backdrop) backdrop.hidden = true; const detail = byId('wbLeadDetail'); if (detail?.classList.contains('wb-drawer-open')) { window.WorkspaceUI?.closeDialog(detail); detail.classList.remove('wb-drawer-open'); } }
    if (page === 'templates') templatesUpdated();
    if (page === 'outreach' && authenticated) loadOutreachHistory();
    byId('status').hidden = page !== 'new';
    if (page === 'new' && byId('status').dataset.taskStatus === 'completed' && !currentTaskSnapshot?.isRunning && !byId('searchBtn').disabled && !/\bstatus-(error|running)\b/.test(byId('status').className)) setStatus('尚未开始任务。确认搜索计划后才会开始采集。');
    updateCrossLink();
  }
  function renderTaskTable() {
    const container = byId('wbTaskTable'); if (!container) return;
    const query = byId('wbTaskSearch').value.trim().toLowerCase();
    const tasks = state.tasks.filter(task => `${task.id} ${task.keyword || ''} ${taskTitle(task)}`.toLowerCase().includes(query));
    byId('wbTaskMetrics').innerHTML = [[state.tasks.length, '最近任务'], [state.tasks.filter(task => task.status === 'running').length, '正在运行'], [state.tasks.reduce((sum, task) => sum + Number(task.leadsCount || 0), 0), '需求候选'], [state.tasks.filter(task => ['partial', 'failed', 'needs_action', 'interrupted'].includes(task.status)).length, '需关注任务']].map(([value, label]) => `<div class="wb-metric"><span>${label}</span><strong>${fmt(value)}</strong></div>`).join('');
    if (!tasks.length) { container.innerHTML = `<div class="workspace-empty wb-empty"><span class="wb-empty-icon">⌕</span><h3>${query ? '没有匹配的任务' : '开始你的第一次研究'}</h3><p>${query ? '换一个关键词或任务编号试试。' : '输入研究目标，确认计划后开始采集。每条候选都保留来源。'}</p>${query ? '' : '<button type="button" onclick="WorkbenchWorkspace.newTask()">新建研究任务</button>'}</div>`; return; }
    container.innerHTML = `<div class="wb-table-scroll"><table class="workspace-table wb-task-table"><thead><tr><th>任务 / 研究目标</th><th>平台</th><th>状态</th><th>笔记</th><th>候选</th><th>更新时间</th><th><span class="wb-sr-only">操作</span></th></tr></thead><tbody>${tasks.map(task => `<tr><td><a href="#/reviews?task=${encodeURIComponent(task.id)}" class="wb-task-title">${esc(taskTitle(task))}</a><span class="wb-table-meta">#${esc(task.id)} · ${esc(task.keyword || '未记录种子词')}</span></td><td>${esc((task.platforms || task.confirmedPlan?.platforms || []).map(platformLabel).join('、') || '未记录')}</td><td><span class="wb-status wb-status-${esc(task.status)}">${esc(taskStatusLabel(task.status))}</span></td><td>${fmt(task.notesCount)}</td><td>${fmt(task.leadsCount)}</td><td class="wb-muted">${esc(fullTime(task.updatedAt))}</td><td><a href="#/reviews?task=${encodeURIComponent(task.id)}" class="wb-text-link">查看 →</a></td></tr>`).join('')}</tbody></table></div>`;
  }
  function fullTime(value) { if (!value) return '未记录'; const date = new Date(Number(value) * 1000); return Number.isNaN(date.valueOf()) ? '未记录' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  function historyUpdated(tasks) { state.tasks = tasks || []; renderTaskTable(); }
  function taskUpdated(data) {
    if (byId('wbReviewContext')) byId('wbReviewContext').textContent = `任务 #${data.taskId || data.id || currentTaskId} · 对照原文，确认值得跟进的需求`;
    const panel = byId('taskPanel');
    panel.classList.toggle('wb-task-static', !data.isRunning);
    byId('status').hidden = state.page !== 'new';
    if (!panel.querySelector('.wb-progress-disclosure')) {
      const disclosure = make('details', 'wb-progress-disclosure', '<summary>任务说明</summary>');
      disclosure.append(byId('stageCoverage')); panel.append(disclosure);
    }
    byId('taskEvents').hidden = true;
    let insights = byId('wbTaskInsights');
    if (!insights) { insights = make('div', 'wb-task-summary-container'); insights.id = 'wbTaskInsights'; insights.setAttribute('aria-label', '任务结果说明'); panel.prepend(insights); }
    insights.innerHTML = window.TaskInsights?.workbenchSummaryHtml(data) || '';
    byId('stageCoverage').innerHTML = window.TaskInsights?.workbenchDetailsHtml(data) || '';
    updateCrossLink();
  }
  function editingResult() { const element = document.activeElement; return element && byId('output')?.contains(element) && element.closest('.lead-actions') && ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName); }
  function shouldDeferResults(data) {
    const signature = `${currentTaskId}:${leadViewFilter}:${JSON.stringify(data)}`;
    if (state.signature === signature && byId('wbLeadRows')) return true;
    if (!state.restoringRoute && editingResult() && renderedTaskId === currentTaskId) { state.pendingResult = { taskId: String(currentTaskId), data }; return true; }
    state.signature = signature;
    return false;
  }
  function flushPendingResults() {
    if (!state.pendingResult || editingResult()) return;
    const pending = state.pendingResult; state.pendingResult = null;
    if (pending.taskId !== String(currentTaskId)) return;
    render(pending.data);
  }
  function taskSwitching() {
    state.pendingResult = null; state.signature = ''; state.selectedLead = '';
    const detail = byId('wbLeadDetail'); if (detail?.classList.contains('wb-drawer-open')) window.WorkspaceUI?.closeDialog(detail);
    const backdrop = byId('wbDetailBackdrop'); if (backdrop) backdrop.hidden = true;
    if (!outreachBusy) { closeDmDraft(); outreachContext = null; if (!outreachSubmitted) preparedOutreach = null; }
  }
  function resultsUpdated(data) {
    const panel = document.querySelector('[data-result-panel="leads"]'), list = panel?.querySelector('.lead-list');
    if (!list) return;
    const summary = byId('output').querySelector(':scope > .summary');
    if (summary) { const context = make('details', 'wb-result-context', '<summary>任务目标与结果说明</summary>'); summary.before(context); context.append(summary); const note = byId('output').querySelector(':scope > .small'); if (note) context.append(note); }
    const toolbar = make('div', 'wb-review-toolbar', '<input id="wbLeadSearch" type="search" aria-label="搜索候选" placeholder="搜索需求、原文或来源" /><select id="wbReviewFilter" aria-label="按人工审核筛选"><option value="all">全部审核状态</option><option value="pending">待人工审核</option><option value="approved">人工通过</option><option value="rejected">人工驳回</option></select><span id="wbLeadResultCount" class="wb-muted"></span>');
    list.before(toolbar);
    const classification = byId('leadFilter');
    if (classification) { classification.setAttribute('aria-label', '需求分类'); const label = document.querySelector('label[for="leadFilter"]'); if (label) label.remove(); const explanation = classification.nextElementSibling; if (explanation?.classList.contains('small')) explanation.remove(); toolbar.prepend(classification); }
    const split = make('div', 'wb-review-split'); list.before(split);
    const queue = make('div', 'wb-lead-queue'); queue.id = 'wbLeadRows'; split.append(queue, list);
    list.classList.add('wb-lead-detail'); list.setAttribute('aria-label', '候选详情');
    list.id = 'wbLeadDetail';
    list.prepend(make('div', 'wb-detail-header', '<span>候选详情 · 原文核验</span><button type="button" aria-label="关闭" onclick="WorkbenchWorkspace.closeDetail()">' + icon('close') + '</button>'));
    list.append(make('div', 'wb-detail-empty wb-empty', '<h3>选择一条候选</h3><p>在左侧选择需求，核对证据后记录人工审核结论。</p>'));
    if (!byId('wbDetailBackdrop')) { const backdrop = make('div', 'wb-detail-backdrop'); backdrop.id = 'wbDetailBackdrop'; backdrop.hidden = true; backdrop.addEventListener('click', closeDetail); document.body.append(backdrop); }
    const heading = panel.querySelector('.section-title'); heading.classList.add('wb-results-toolbar');
    const exportScope = byId('exportScope'), exportLabel = document.querySelector('label[for="exportScope"]');
    const exportBar = make('div', 'wb-export-bar'); exportBar.id = 'wbExportBar';
    exportBar.append(exportLabel, exportScope); const count = make('span', 'wb-export-count'); count.id = 'wbExportCount'; exportBar.append(count);
    const exportButtons = heading.querySelector('.btn-row'); if (exportButtons) exportBar.append(exportButtons); heading.after(exportBar);
    [...list.querySelectorAll('.lead-card')].forEach(card => {
      const index = Number(card.dataset.leadIndex), lead = data.leads.leads[index];
      card.dataset.leadId = lead.lead_id;
      const review = card.querySelector('.lead-actions'), feedback = card.querySelectorAll('.lead-actions')[1], touch = card.querySelector('.touch-actions');
      const actions = make('div', 'wb-review-save-row');
      const save = review.querySelector('button'); const history = review.querySelectorAll('button')[1];
      save.classList.add('wb-review-save'); history.classList.add('wb-review-history');
      actions.append(history, save); const next = make('button', 'primary wb-review-next', '保存并下一条'); next.type = 'button'; next.onclick = () => saveAndNext(index); actions.append(next); review.append(actions);
      const feedbackDisclosure = make('details', 'wb-feedback-disclosure', '<summary>反馈识别问题 <span>供维护人员改进，不改变审核结论</span></summary>'); feedback.before(feedbackDisclosure); feedbackDisclosure.append(feedback);
      const touchDisclosure = make('details', 'wb-touch-disclosure', '<summary>触达此候选 <span>每个动作都需单独预览和确认</span></summary>'); touch.before(touchDisclosure); touchDisclosure.append(touch);
      review.before(make('div', 'wb-detail-section-heading', '<h4>人工审核</h4><p>对照原文确认后，记录你的判断。</p>'));
      // Keep the review immediately after evidence. Outreach and model feedback are secondary.
      card.append(touchDisclosure, feedbackDisclosure, actions);
      card.querySelectorAll('textarea').forEach(field => { if (!field.getAttribute('aria-label')) field.setAttribute('aria-label', field.id.startsWith('review') ? '人工审核理由' : '模型反馈说明'); });
      const feedbackSelect = card.querySelector('[id^="feedbackType-"]'); if (feedbackSelect) feedbackSelect.setAttribute('aria-label', '模型反馈类型');
      const reviewStatus = byId(`reviewStatus-${index}`), reviewNote = byId(`reviewNote-${index}`), reviewResult = byId(`reviewResult-${index}`);
      reviewResult.setAttribute('role', 'status'); reviewResult.setAttribute('aria-live', 'polite');
      const updateDirty = () => {
        const dirty = reviewStatus.value !== (lead.review?.status || 'pending') || reviewNote.value.trim() !== (lead.review?.note || '').trim();
        reviewResult.classList.toggle('wb-unsaved', dirty);
        reviewResult.textContent = dirty ? '审核修改尚未保存' : `${reviewLabel(lead.review?.status)} · 无未保存修改`;
      };
      reviewStatus.addEventListener('change', updateDirty); reviewNote.addEventListener('input', updateDirty); updateDirty();
    });
    byId('wbLeadSearch').value = state.query; byId('wbReviewFilter').value = state.review;
    byId('wbLeadSearch').addEventListener('input', event => { state.query = event.target.value; renderLeadQueue(); });
    byId('wbReviewFilter').addEventListener('change', event => { state.review = event.target.value; renderLeadQueue(); });
    renderLeadQueue(); exportUpdated();
    const tab = route().tab || 'leads'; const previousRouting = state.routing; state.routing = true; try { switchResultTab(tab); } finally { state.routing = previousRouting; }
  }
  function reviewScopeEntries() {
    const query = state.query.trim().toLowerCase();
    return filteredLeadEntries().filter(({ lead }) => !query || `${lead.demand_summary || ''} ${lead.evidence || ''} ${lead.note_title || ''} ${lead.nickname || ''}`.toLowerCase().includes(query));
  }
  function leadReviewStatus(lead) { return ['approved','rejected'].includes(lead.review?.status) ? lead.review.status : 'pending'; }
  function visibleEntries() {
    return reviewScopeEntries().filter(({ lead }) => state.review === 'all' || leadReviewStatus(lead) === state.review).sort((a,b) => {
      // Rank only the pending-demand view. Preserve the immutable original indices for all actions.
      if (!['uncertain','pending'].includes(leadViewFilter) || !window.TaskInsights) return 0;
      if (leadViewFilter === 'pending' && isQualifiedLeadForDisplay(a.lead) !== isQualifiedLeadForDisplay(b.lead)) return isQualifiedLeadForDisplay(a.lead) ? -1 : 1;
      return TaskInsights.inquiry(a.lead).rank - TaskInsights.inquiry(b.lead).rank;
    });
  }
  function renderLeadQueue() {
    const container = byId('wbLeadRows'); if (!container) return;
    const entries = visibleEntries();
    const counts = {pending:0,approved:0,rejected:0};
    reviewScopeEntries().forEach(({lead}) => { counts[leadReviewStatus(lead)]++; });
    for (const [status,label] of Object.entries({pending:'待你审核',approved:'已通过',rejected:'已排除'})) {
      const button = byId(`wbReviewCount-${status}`);
      if (!button) continue;
      button.innerHTML = `<strong>${fmt(counts[status])}</strong><span>${label}</span>`;
      button.setAttribute('aria-pressed', String(state.review === status));
      button.setAttribute('aria-label', `${label} ${counts[status]} 条`);
    }
    const scopeLabel = {pending:'合格与待确认需求',qualified:'合格定制需求',uncertain:'待确认需求',all:'全部分析结果',demand:'明确需求',information:'经验或知识讨论',promotion:'供方宣传',irrelevant:'无关内容'}[leadViewFilter] || '当前需求分类';
    if (byId('wbReviewScope')) byId('wbReviewScope').textContent = `统计范围：${scopeLabel}${state.query.trim() ? ' · 当前搜索结果' : ''}。点击数量筛选，下方显示 ${entries.length} 条。`;
    if (byId('wbStartReview')) byId('wbStartReview').disabled = counts.pending === 0;
    byId('wbLeadResultCount').textContent = `${entries.length} 条候选`;
    container.innerHTML = entries.length ? entries.map(({ lead, index }) => `<button type="button" class="wb-lead-row" data-wb-lead="${esc(lead.lead_id)}" data-index="${index}"><span class="wb-lead-row-top"><span class="wb-platform">${esc(platformLabel(leadPlatform(lead)))}</span><span class="wb-review-state wb-review-${esc(lead.review?.status || 'pending')}">${reviewLabel(lead.review?.status)}</span></span><strong>${esc(lead.demand_summary || lead.evidence || '未提炼需求')}</strong><span class="wb-lead-evidence">${esc(lead.evidence || '未记录原文证据')}</span><span class="wb-lead-row-bottom"><span>${esc(demandLabel(lead.classification))}</span><span>${fmt(lead.score)} 分 · ${esc(priorityLabel(lead.priority))}</span></span>${window.TaskInsights?.inquiry(lead).pending ? `<span class="wb-inquiry-meta">${esc(TaskInsights.inquiry(lead).label)} · ${esc(TaskInsights.inquiry(lead).priorityLabel)}</span>` : ''}</button>`).join('') : '<div class="wb-empty"><h3>当前筛选没有候选</h3><p>试试其他分类或审核状态；没有合格需求时不会填充无关内容。</p></div>';
    container.querySelectorAll('[data-wb-lead]').forEach(button => button.addEventListener('click', () => selectLead(button.dataset.wbLead)));
    if (state.selectedLead && !entries.some(({ lead }) => String(lead.lead_id) === String(state.selectedLead))) state.selectedLead = '';
    selectLead(state.selectedLead, false);
  }
  function filterReviews(status) {
    if (!['all','pending','approved','rejected'].includes(status)) return;
    state.review = status;
    if (byId('wbReviewFilter')) byId('wbReviewFilter').value = status;
    renderLeadQueue();
  }
  function startReview() {
    filterReviews('pending');
    switchResultTab('leads');
    const first = visibleEntries()[0];
    if (first) { selectLead(first.lead.lead_id); byId('wbLeadDetail')?.scrollIntoView({block:'nearest'}); }
  }
  function selectLead(id, updateRoute = true) {
    state.selectedLead = String(id || '');
    document.querySelectorAll('.wb-lead-detail .lead-card').forEach(card => { const active = card.dataset.leadId === state.selectedLead; card.hidden = !active; card.classList.toggle('wb-active-lead', active); });
    document.querySelectorAll('[data-wb-lead]').forEach(button => { const active = button.dataset.wbLead === state.selectedLead; button.classList.toggle('selected', active); button.setAttribute('aria-pressed', String(active)); });
    const detail = byId('wbLeadDetail');
    if (detail) {
      detail.querySelector('.wb-detail-empty').hidden = Boolean(state.selectedLead);
      if (compact()) {
        const visible = Boolean(state.selectedLead) && state.page === 'reviews' && (!route().tab || route().tab === 'leads');
        byId('wbDetailBackdrop').hidden = !visible;
        if (visible) { detail.classList.add('wb-drawer-open'); window.WorkspaceUI?.showDialog(detail); }
        else { detail.classList.remove('wb-drawer-open'); window.WorkspaceUI?.closeDialog(detail); }
      } else { if (detail.classList.contains('wb-drawer-open')) window.WorkspaceUI?.closeDialog(detail); detail.style.display = ''; detail.classList.remove('wb-drawer-open'); byId('wbDetailBackdrop').hidden = true; detail.removeAttribute('aria-modal'); detail.setAttribute('role', 'region'); }
      layoutDetail();
    }
    if (updateRoute && !state.routing) navigate('reviews', params());
  }
  function closeDetail() {
    const selected = state.selectedLead;
    captureResultFormState();
    state.selectedLead = '';
    const detail = byId('wbLeadDetail'); if (detail) window.WorkspaceUI?.closeDialog(detail);
    selectLead('', false);
    if (state.page === 'reviews') navigate('reviews', { taskId: currentTaskId, tab: 'leads' });
    const row = [...document.querySelectorAll('[data-wb-lead]')].find(button => button.dataset.wbLead === selected); row?.focus({ preventScroll: true });
  }
  function layoutDetail() {
    const detail = byId('wbLeadDetail'); if (!detail?.getBoundingClientRect) return;
    detail.style.maxHeight = compact() ? '' : `${Math.max(0, window.innerHeight - Math.max(0, detail.getBoundingClientRect().top) - 16)}px`;
  }
  async function saveAndNext(index) {
    const entries = visibleEntries(), position = entries.findIndex(entry => entry.index === index), next = entries[position + 1]?.lead.lead_id;
    const card = document.querySelector(`.lead-card[data-lead-index="${index}"]`); const buttons = card ? [...card.querySelectorAll('.wb-review-save-row button')] : [];
    buttons.forEach(button => { button.disabled = true; });
    try { if (await saveReview(index)) { if (next) selectLead(next); else notify('审核已保存，已到当前列表最后一条。', 'success'); } }
    finally { buttons.forEach(button => { button.disabled = false; }); }
  }
  function reviewSaved(index) { if (currentTaskSnapshot && cachedData) { currentTaskSnapshot.leads = cachedData.leads; taskUpdated(currentTaskSnapshot); } byId(`reviewResult-${index}`)?.classList.remove('wb-unsaved'); renderLeadQueue(); exportUpdated(); notify('人工审核已保存。', 'success'); }
  function exportUpdated() { const count = byId('wbExportCount'); if (count) count.textContent = `本任务将导出 ${selectedExportLeads().length} 条 · ${exportScopeLabel()}（独立于列表筛选）`; }
  function templatesUpdated() {
    const container = byId('wbTemplateCards'); if (!container) return;
    container.innerHTML = taskTemplates.length ? taskTemplates.map((template, index) => `<article class="workspace-card wb-template-card"><span class="wb-template-icon">${icon('templates')}</span><h2>${esc(template.name)}</h2><p>${esc(template.plan?.searchGoal || '复用已保存的采集配置')}</p><div class="wb-template-meta">${esc(offeringLabel(template.plan?.offeringId))} · 最多 ${fmt(template.plan?.maxNotes || 20)} 篇笔记</div><button type="button" class="btn-secondary" data-template-index="${index}">使用模板 →</button></article>`).join('') : '<div class="workspace-empty wb-empty"><h3>保存常用研究方式</h3><p>在新建任务的“目标范围”步骤，填写配置后选择“保存任务模板”。</p><button type="button" class="primary" onclick="WorkbenchWorkspace.newTask()">新建研究任务</button></div>';
    container.querySelectorAll('[data-template-index]').forEach(button => button.addEventListener('click', () => { byId('taskTemplate').value = button.dataset.templateIndex; loadTaskTemplate(); step(1, false); navigate('new'); }));
  }
  function connectionUpdated(connected, message = '') {
    const banner = byId('wbConnection'); if (!banner) return;
    if (connected) { state.lastSync = new Date(); banner.hidden = true; }
    else { banner.hidden = false; setSystemMessage(byId('wbConnectionText'), `连接中断${state.lastSync ? ` · 最后同步 ${state.lastSync.toLocaleTimeString('zh-CN')}` : ''}。已保存结果仍可查看，任务状态将在恢复连接后核实。${message ? ' ' + message : ''}`); }
  }
  function reviewHistory(history) { byId('wbReviewHistoryBody').innerHTML = history.length ? `<ol class="wb-audit-timeline">${history.map(item => `<li><strong>${reviewLabel(item.status)}</strong><time>${esc(fullTime(item.created))}</time><p>${esc(item.note || '无备注')}</p></li>`).join('')}</ol>` : '<div class="wb-empty">尚无人工审核记录。</div>'; window.WorkspaceUI.showDialog(byId('wbReviewHistoryDialog')); }
  function updateCrossLink() { const link = byId('wbHarnessLink'); if (link) link.href = `harness.html#/runs${currentTaskId ? '?task=' + encodeURIComponent(currentTaskId) : ''}`; }
  window.WorkbenchWorkspace = {
    initialize, historyUpdated, taskUpdated, resultsUpdated, shouldDeferResults, flushPendingResults, taskSwitching, reviewSaved, exportUpdated, templatesUpdated, connectionUpdated, reviewHistory,
    step, selectLead, closeDetail, filterReviews, startReview,
    revealField(id) { const field = byId(id); if (!field) return; if (byId('wbConfiguration').contains(field)) step(1, false); let ancestor = field.parentElement; while (ancestor) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; } field.focus(); },
    inputsChanged() { const summary = byId('wbBudgetSummary'); if (summary) summary.textContent = `当前预算：最多 ${byId('count').value} 篇笔记 · ${byId('maxCommentsTotal').value} 条评论 · ${byId('maxKeywords').value} 个搜索词`; },
    planInvalidated() { if (state.ready && byId('wbConfirmation')) step(1, false); },
    newTask() { if (currentTaskSnapshot?.isRunning) { notify('当前任务仍在运行，可先准备下一次研究目标；运行结束后再生成计划。'); } step(1, false); navigate('new'); },
    newTemplate() { this.newTask(); notify('填写目标与采集范围后，选择“保存任务模板”。'); },
    planReady() { step(2, false); navigate('new'); },
    taskStarted(id) { state.signature = ''; state.selectedLead = ''; navigate('reviews', { taskId: id }); },
    taskOpened(id) { state.signature = ''; if (!state.routing) navigate('reviews', { taskId: id }); },
    tabChanged(tab) { if (state.ready && !state.routing && state.page === 'reviews') { const current = route(); if (current.tab !== tab) navigate('reviews', { ...params(), tab }); } },
    authChanged(ready) { document.body.classList.toggle('wb-signed-out', !ready); byId('wbConfiguration').hidden = !ready; document.querySelector('.wb-sidebar').classList.toggle('wb-locked', !ready); if (ready) { templatesUpdated(); applyRoute(); } },
    toggleNav() { setNav(!byId('wbSidebar')?.classList.contains('is-open')); },
    refreshConnection() { if (state.page === 'tasks') return loadTaskHistory(); if (state.page === 'outreach') return loadOutreachHistory(); if (state.page === 'templates') return renderTemplates().then(templatesUpdated); if (currentTaskId) (currentTaskSnapshot?.isRunning || currentTaskSnapshot?.isEvolving) ? startPolling(currentTaskId) : pollStatus(currentTaskId); else loadTaskHistory(); }
  };
  initialize();
})();
