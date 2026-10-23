/* Administration workspace: existing APIs, task-scoped navigation and read-only sync. */
'use strict';
let adminTasks = [];
let adminSettingsDirty = false;
let adminSettingsSaving = false;
let adminSettingsSnapshot = '';
let adminSettingsLoading = null;
let adminLastSyncedAt = 0;
let adminPollTimer = null;
let adminStatusRequest = null;
let adminPendingTask = '';
let adminTaskGeneration = 0;
let adminSelectedPromptId = '';
let adminAuditGeneration = 0;
let adminHistoryGeneration = 0;
let adminReflectionTaskId = '';
const ADMIN_PAGES = {
  runs: ['运行监控', '跟踪任务完成范围，检查调用轨迹与决策证据。'],
  evaluations: ['质量评测', '对照原文核验真实样本，为候选版本留下可追溯的验收记录。'],
  versions: ['版本管理', '查看当前版本、候选差异与关联验收，再明确启用。'],
  feedback: ['反馈队列', '将人工反馈转为可验证的版本改进。'],
  settings: ['系统设置', '管理本机模型、平台与安全边界。']
}
const adminRoutePage = page => ({overview:'runs',trace:'runs',audit:'runs',agents:'versions',evaluations:'evaluations',feedback:'feedback',settings:'settings',runs:'runs',versions:'versions'}[page] || 'runs');
const adminInternalPage = route => route.page === 'runs' ? (['trace','audit'].includes(route.tab) ? route.tab : 'overview') : route.page === 'versions' ? 'agents' : ['evaluations','feedback','settings'].includes(route.page) ? route.page : 'overview';
let adminInitialRoute = WorkspaceUI.route('runs');
if (adminInitialRoute.taskId && /^\d+$/.test(String(adminInitialRoute.taskId))) currentTaskId = String(adminInitialRoute.taskId);
activePage = adminInternalPage(adminInitialRoute);
function adminRouteTo(page, options = {}, replace = false) {
  WorkspaceUI.navigate(page, { taskId: currentTaskId || undefined, ...options }, replace);
  applyAdminRoute();
}
function showHarness() {
  adminReady = true; adminSetupMode = false;
  $('adminGate').style.display = 'none'; $('harnessMain').style.display = 'grid'; $('adminNav').style.display = 'grid';
  applyAdminRoute();
}
function setActivePage(page) {
  const routePage = adminRoutePage(page);
  adminRouteTo(routePage, routePage === 'runs' ? { tab: ['trace','audit'].includes(page) ? page : 'overview' } : {});
}
function toggleAdminNav(open) {
  const wasOpen = $('harnessMain').classList.contains('nav-open');
  const next = typeof open === 'boolean' ? open : !wasOpen;
  const focusWasInside = document.querySelector('.workspace-sidebar').contains?.(document.activeElement);
  $('harnessMain').classList.toggle('nav-open', next); $('adminMenuButton').setAttribute('aria-expanded', String(next));
  document.querySelector('.workspace-sidebar').inert = !next && window.innerWidth < 1024;
  if (next) document.querySelector('.workspace-sidebar .menu-item.active')?.focus();
  else if (wasOpen && focusWasInside) $('adminMenuButton').focus();
}
function activateAdminPanel(page) {
  activePage = page;
  toggleAdminNav(false);
  localStorage.setItem('harnessActivePage', page);
  const routePage = adminRoutePage(page);
  for (const name of ['overview','audit','trace','evaluations','agents','feedback','settings']) {
    const panel = $(`page-${name}`); if (panel) panel.classList.toggle('active', name === page);
  }
  for (const name of ['overview','evaluations','agents','feedback','settings']) {
    const button = $(`nav-${name}`); if (!button) continue;
    const selected = adminRoutePage(name) === routePage;
    button.classList.toggle('active', selected);
    selected ? button.setAttribute('aria-current', 'page') : button.removeAttribute('aria-current');
  }
  $('pageTitle').textContent = ADMIN_PAGES[routePage][0]; $('pageSubtitle').textContent = ADMIN_PAGES[routePage][1];
  $('adminRunTabs').hidden = routePage !== 'runs'; $('adminTaskContext').hidden = routePage !== 'runs';
  document.querySelectorAll('[data-run-tab]').forEach(button => {
    const selected = button.dataset.runTab === page;
    button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected));
  });
  if (page === 'settings' && adminReady) loadSettings();
}
function applyAdminRoute() {
  const route = WorkspaceUI.route('runs'), nextPage = adminInternalPage(route);
  const taskId = route.taskId && /^\d+$/.test(String(route.taskId)) ? String(route.taskId) : currentTaskId;
  const changedTask = taskId && taskId !== currentTaskId;
  if (changedTask) {
    ++adminTaskGeneration; ++adminAuditGeneration; currentTaskId = taskId;
    localStorage.setItem('currentTaskId', currentTaskId); latestTask = null;
    clearTimeout(adminPollTimer); adminPollTimer = null;
    $('currentTask').textContent = `正在读取任务 #${taskId}…`;
    $('auditTaskId').value = taskId; $('auditOffset').value = '0'; auditState.offset = 0; auditState.loaded = false;
  }
  activateAdminPanel(nextPage); updateAdminLinks(); renderAdminHistory();
  if (changedTask) closeAgentCall();
  if (adminReady && changedTask) loadTask(currentTaskId);
  if (adminReady && nextPage === 'audit') {
    if ($('auditTaskId').value !== currentTaskId) { $('auditTaskId').value = currentTaskId; auditState.loaded = false; $('auditOffset').value = '0'; }
    if (!auditState.loaded || changedTask) loadAuditLogs(true);
  }
  if (route.page === 'versions' && route.promptId && latestPrompts.length) openCandidateDetail(route.promptId, false);
  else if (!route.promptId && $('candidateDialog').open) { adminSelectedPromptId = ''; WorkspaceUI.closeDialog($('candidateDialog')); }
}
function updateAdminLinks() {
  const target = `index.html#/tasks${currentTaskId ? `?task=${encodeURIComponent(currentTaskId)}` : ''}`;
  $('workbenchLink').href = target; $('contextWorkbenchLink').href = target;
}
async function selectTask(taskId) {
  const next = String(taskId); if (!/^\d+$/.test(next)) return;
  if (next === currentTaskId) { await loadTask(next); return; }
  adminRouteTo('runs', { taskId: next, tab: ['trace','audit'].includes(activePage) ? activePage : 'overview' });
}
async function loadHistory() {
  if (!adminReady) return;
  const generation = ++adminHistoryGeneration;
  const data = await post('/api/admin/tasks/list', { limit: 100 });
  if (generation !== adminHistoryGeneration || !adminReady) return;
  adminTasks = data.tasks || [];
  const selected = adminTasks.find(t => String(t.id) === currentTaskId);
  if (selected && selected.status !== 'running' && latestTask) latestTask.isRunning = false;
  if (!currentTaskId && adminTasks[0]) {
    currentTaskId = String(adminTasks[0].id); localStorage.setItem('currentTaskId', currentTaskId);
    const route = WorkspaceUI.route('runs'); WorkspaceUI.navigate(adminRoutePage(activePage), {taskId:currentTaskId,tab:route.tab,promptId:route.promptId}, true);
  }
  renderAdminHistory(); updateAdminLinks(); updateAdminSettingsState();
  return adminTasks;
}
function renderAdminHistory() {
  if (!$('history')) return;
  const query = $('adminTaskSearch').value.trim().toLowerCase(), status = $('adminTaskFilter').value;
  const tasks = adminTasks.filter(t => (!status || t.status === status) && (!query || [t.id,t.keyword,t.demandDescription].join(' ').toLowerCase().includes(query)));
  $('adminTaskCount').textContent = `${tasks.length} / ${adminTasks.length} 个任务`;
  $('history').innerHTML = tasks.length ? `<div class="admin-table-scroll"><table class="workspace-table admin-table"><thead><tr><th>任务</th><th>状态</th><th>笔记 / 候选</th><th>最近更新</th><th><span class="admin-sr-only">查看</span></th></tr></thead><tbody>${tasks.map(t => `<tr class="${String(t.id) === currentTaskId ? 'is-selected' : ''}"><td><button class="admin-text-button" onclick="selectTask(${Number(t.id)})">${esc(t.keyword || t.demandDescription || '关键词任务')}</button><div class="small">#${fmt(t.id)}</div></td><td><span class="pill ${t.status === 'completed' ? 'pass' : t.status === 'failed' ? 'drop' : 'pending'}">${esc(statusLabel(t.status))}</span></td><td>${fmt(t.notesCount)} / ${fmt(t.leadsCount)}</td><td class="small">${formatTime(t.updatedAt)}</td><td><button onclick="selectTask(${Number(t.id)})">${String(t.id) === currentTaskId ? '当前任务' : '查看'}</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="workspace-empty">${adminTasks.length ? '没有匹配的任务，请调整筛选。' : '暂无任务。在工作台确认搜索计划后，运行会出现在这里。'}</div>`;
}
function adminSetSync(message, error = false) {
  if (!error && feedbackLoadError && message !== '正在同步…') {
    message = '反馈队列同步失败，请检查连接后刷新重试'; error = true;
  }
  $('adminSyncStatus').textContent = message; $('adminSyncStatus').classList.toggle('admin-error', error);
}
function scheduleAdminPoll() {
  clearTimeout(adminPollTimer); adminPollTimer = null;
  if (!adminReady || document.hidden || !(latestTask?.isRunning || latestTask?.isEvolving) || String(latestTask.taskId) !== currentTaskId) return;
  adminPollTimer = setTimeout(() => { adminPollTimer = null; loadTask(currentTaskId); }, 2000);
}
function loadTask(taskId) {
  if (!adminReady || !taskId) {
    if (!taskId) $('currentTask').textContent = '暂无任务。请先在工作台确认搜索计划。';
    return Promise.resolve();
  }
  adminPendingTask = String(taskId); clearTimeout(adminPollTimer); adminPollTimer = null;
  if (adminStatusRequest) return adminStatusRequest;
  adminStatusRequest = (async () => {
    while (adminPendingTask && adminReady) {
      const requestedId = adminPendingTask, generation = adminTaskGeneration;
      adminPendingTask = '';
      try {
        const data = await post('/api/admin/tasks/status', { taskId: requestedId });
        if (!adminReady || generation !== adminTaskGeneration || requestedId !== currentTaskId) continue;
        if (!data || data.ok === false || data.hasTask === false) {
          latestTask = null; $('currentTask').textContent = '没有找到此任务。请选择其他运行。'; adminSetSync('任务不可用', true); continue;
        }
        latestTask = data; adminLastSyncedAt = Date.now(); renderTask(data);
        adminSetSync(`同步于 ${new Date(adminLastSyncedAt).toLocaleTimeString('zh-CN', {hour12:false})}`);
        const index = adminTasks.findIndex(t => String(t.id) === requestedId);
        if (index >= 0) adminTasks[index] = {...adminTasks[index], status:data.status, updatedAt:data.updatedAt};
        renderAdminHistory(); updateAdminSettingsState();
      } catch (error) {
        if (generation === adminTaskGeneration && requestedId === currentTaskId && adminReady) adminSetSync(`同步失败 · ${DisplayText.message(error.message)} · 保留上次数据`, true);
      }
    }
  })().finally(() => { adminStatusRequest = null; scheduleAdminPoll(); });
  return adminStatusRequest;
}
async function refreshAll() {
  if (!adminReady) return;
  const button = $('adminRefreshBtn'); if (button.disabled) return;
  button.disabled = true; adminSetSync('正在同步…');
  try {
    const reads = await Promise.allSettled([loadAgents(), loadPrompts(), loadFeedback(), loadHistory(), loadEvaluationData()]);
    await loadTask(currentTaskId);
    if (activePage === 'audit') await loadAuditLogs(false);
    if (activePage === 'settings' && !settingsLoaded) await loadSettings();
    if (reads.some(r => r.status === 'rejected')) adminSetSync('部分数据同步失败，请重试', true);
    else if (!currentTaskId) adminSetSync('数据已同步 · 暂无任务');
    applyAdminRoute();
  } finally { button.disabled = false; }
}
function adminTraceSummary(value) {
  if (typeof value === 'string') {
    try { value = JSON.parse(value); } catch { return DisplayText.report(value).slice(0,600); }
  }
  if (!value || typeof value !== 'object') return '没有已保存的输出摘要。';
  for (const key of ['summary','task_summary','demand_summary','decision','reason','executive_summary']) {
    if (value[key] && (typeof value[key] === 'string' || typeof value[key] === 'object')) return (key === 'decision' && typeof value[key] === 'string' ? DisplayText.code(value[key], '已返回判断') : DisplayText.report(DisplayText.humanize(value[key]))).slice(0,600);
  }
  const labels = {notes:'篇笔记判断',leads:'条候选判断',queries:'条搜索规划',agent_summaries:'项环节总结',warnings:'条风险提示'};
  const counts = Object.entries(labels).filter(([key]) => Array.isArray(value[key])).map(([key,label]) => `${value[key].length} ${label}`);
  return counts.length ? `已返回 ${counts.join('、')}。` : '已返回结构化输出，展开完整调用输出查看详细内容。';
}
function adminTraceRecord(value, missingText) {
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) return `<p class="small">${esc(missingText)}</p>`;
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return `<pre>${esc(text)}</pre>`;
}
function renderTask(data) {
  renderTaskSummary(data);
  if (window.TaskInsights) $('harnessStageCoverage').innerHTML += TaskInsights.collectionHtml(data);
  renderTaskInsights(data);
  $('currentTask').innerHTML = `<div><span class="small">当前任务 #${fmt(data.taskId)}</span><strong>${esc(adminTasks.find(t => String(t.id) === String(data.taskId))?.keyword || data.confirmedPlan?.searchGoal || '关键词任务')}</strong></div><div><span class="pill ${data.status === 'completed' ? 'pass' : data.status === 'failed' ? 'drop' : 'pending'}">${esc(statusLabel(data.status))}</span><span class="small">任务更新于 ${formatTime(data.updatedAt)}</span></div>`;
  const trace = data.agentTrace || [];
  $('trace').innerHTML = trace.length ? trace.map((run,index) => `<details class="admin-trace-row"><summary><span><strong>${esc(agentLabel(run.agentKey || run.agentName))}</strong><span class="small">${esc(purposeLabel(run.purpose))}</span></span><span class="small">${esc(run.promptVersion || '版本未记录')} · ${fmt(run.durationMs)} 毫秒</span><span class="pill ${run.error ? 'drop' : 'pass'}">${run.error ? '失败' : '已返回'}</span></summary><div class="admin-trace-detail"><p class="small">批次 ${esc(DisplayText.code(run.runType || 'main', '任务执行'))} ${esc(run.runId || '')} · ${formatTime(run.createdAt)}</p>${run.error ? `<p class="admin-error">${esc(DisplayText.message(run.error))}</p>` : ''}<div class="admin-readable">${esc(adminTraceSummary(run.outputSummary))}</div><details class="admin-raw"><summary>调用输入 · 已保存摘要${run.inputSummaryTruncated ? '（有截断）' : ''}</summary>${adminTraceRecord(run.inputSummary, '此运行未保存输入摘要，无法从现有记录恢复。')}</details><details class="admin-raw"><summary>调用输出 · 已保存摘要${run.outputSummaryTruncated ? '（有截断）' : ''}</summary>${adminTraceRecord(run.outputSummary, '此运行未保存输出记录。')}</details>${run.hasFullAudit && run.callId ? `<button onclick="openAgentCall(${index})">查看完整输入输出</button>` : '<p class="small">历史记录未保存完整调用审计，现有摘要可能不完整，不能据此精确重放。</p>'}</div></details>`).join('') : '<div class="workspace-empty">此任务还没有调用轨迹。</div>';
}
let agentCallReadGeneration = 0;
function closeAgentCall() { ++agentCallReadGeneration; if ($('agentCallDialog').open) WorkspaceUI.closeDialog($('agentCallDialog')); }
async function openAgentCall(index) {
  const run = latestTask?.agentTrace?.[index], taskId = String(currentTaskId || '');
  if (!run?.hasFullAudit || !run.callId || String(latestTask.taskId) !== taskId) return adminNotice('此记录没有可读取的完整调用审计。');
  const generation = ++agentCallReadGeneration;
  $('agentCallTitle').textContent = `${agentLabel(run.agentKey || run.agentName)} · 完整调用记录`;
  $('agentCallBody').textContent = '正在读取本地保存的完整审计…';
  if (!$('agentCallDialog').open) WorkspaceUI.showDialog($('agentCallDialog'));
  try {
    const record = await get(`/api/agents/call?taskId=${encodeURIComponent(taskId)}&callId=${encodeURIComponent(run.callId)}`);
    if (generation !== agentCallReadGeneration || taskId !== String(currentTaskId)) return;
    $('agentCallBody').innerHTML = `<p class="small">任务 #${esc(taskId)} · 调用 ${esc(record.callId || run.callId)} · ${esc(DisplayText.status(record.status))}。以下为已保存记录；读取不会重新执行。</p>${[['input','完整输入'],['request','模型请求'],['output','完整输出'],['metrics','耗时与用量']].map(([key,label]) => `<details class="admin-raw"><summary>${label}</summary>${adminTraceRecord(record[key], '此项未保存，不能从摘要恢复。')}</details>`).join('')}`;
  } catch (error) { if (generation === agentCallReadGeneration && taskId === String(currentTaskId)) $('agentCallBody').textContent = `读取失败：${DisplayText.message(error.message)}。没有重新执行模型调用。`; }
}
function renderTaskInsights(data = latestTask) {
  if (!data || !window.TaskInsights) return;
  $('adminTaskInsights').innerHTML = TaskInsights.summaryHtml(data, latestPrompts, evaluationRuns);
  const state = TaskInsights.evolution(data, latestPrompts, evaluationRuns);
  $('adminEvolution').innerHTML = `<div class="admin-section-heading"><strong>${esc(state.label)}</strong><span class="small">候选生成与启用分开记录</span></div><p class="small">${esc(state.detail)}</p>${state.record.summary ? `<p>${esc(DisplayText.report(DisplayText.humanize(state.record.summary)))}</p>` : ''}<div class="admin-evolution-candidates">${state.candidates.map(item => `<button onclick="openTaskCandidate(${item.promptId})">候选 #${item.promptId} · ${esc(item.label)}</button>`).join('')}</div><p class="small">复盘与反馈分析只生成建议或候选。评测、逐条人工验收、明确启用完成后，新任务才使用新版本。</p><button onclick="setActivePage('feedback')">查看人工反馈与分析</button>`;
  const button = $('reflectTaskBtn');
  button.disabled = Boolean(data.isRunning || data.isEvolving || adminReflectionTaskId || !data.taskId);
  button.textContent = data.isEvolving || adminReflectionTaskId === String(data.taskId) ? '正在复盘…' : ['failed','interrupted'].includes(state.record.status) ? '重试任务复盘' : '复盘此任务';
}
async function openTaskCandidate(promptId) {
  const taskId = String(currentTaskId);
  try {
    await Promise.all([loadPrompts(),loadEvaluationData()]);
    if (String(currentTaskId) === taskId) openCandidateDetail(promptId);
  } catch (error) { if (String(currentTaskId) === taskId) adminNotice(`读取候选失败：${DisplayText.message(error.message)}`,'error'); }
}
async function reflectCurrentTask() {
  const taskId = String(currentTaskId || '');
  if (adminReflectionTaskId || !/^\d+$/.test(taskId)) return;
  if (!await adminMutationAllowed()) return;
  if (!await WorkspaceUI.confirm({title:'复盘当前任务',message:`将分析任务 #${taskId} 已保存的执行与质量证据，并实际调用模型生成改进建议。候选不会自动启用；已有任务结果不会重新采集。`,confirmText:'开始复盘'})) return;
  if (taskId !== String(currentTaskId)) return adminNotice('当前任务已切换，请重新确认。');
  adminReflectionTaskId = taskId; renderTaskInsights();
  try {
    const response = await post('/api/evolution/reflect', {taskId:Number(taskId),...(['failed','interrupted'].includes(latestTask?.evolution?.status) ? {retry:true} : {})});
    const result = response.evolution || response;
    if (String(currentTaskId) === taskId && latestTask) {
      latestTask.evolution = result; latestTask.isEvolving = response.isEvolving === true || result.status === 'running'; renderTaskInsights();
    }
    await Promise.all([loadPrompts(),loadEvaluationData()]);
    if (String(currentTaskId) === taskId) await loadTask(taskId);
    adminNotice(result.status === 'failed' ? `复盘未完成：${DisplayText.message(result.error || '请查看运行日志')}` : result.status === 'running' ? '复盘已开始，进度将自动更新。' : '复盘结果已记录；候选仍需评测、人工验收和明确启用。', result.status === 'failed' ? 'error' : 'success');
  } catch (error) { adminNotice(`复盘失败：${DisplayText.message(error.message)}。未启用任何版本。`,'error'); }
  finally { adminReflectionTaskId = ''; renderTaskInsights(); scheduleAdminPoll(); }
}
async function loadAuditLogs(reset = false) {
  if (!adminReady) return;
  const taskId = currentTaskId; if (!taskId) { $('auditStatus').textContent = '请先选择任务。'; return; }
  const generation = ++adminAuditGeneration;
  $('auditTaskId').value = taskId;
  if (reset) { auditState.offset = 0; $('auditOffset').value = '0'; }
  const payload = auditPayload(); payload.taskId = Number(taskId);
  auditState.loading = true; $('auditQueryBtn').disabled = true; $('auditStatus').textContent = '正在读取审计记录…';
  try {
    const data = await post('/api/admin/tasks/audit', payload);
    if (generation !== adminAuditGeneration || taskId !== currentTaskId || !adminReady) return;
    auditState.loaded = true; renderAuditLogs(auditItemsFrom(data), data || {});
  } catch (error) { if (generation === adminAuditGeneration) { auditState.loaded = false; $('auditStatus').textContent = `读取失败：${DisplayText.message(error.message)}。保留上次结果。`; } }
  finally { if (generation === adminAuditGeneration) { auditState.loading = false; $('auditQueryBtn').disabled = false; } }
}
function useCurrentTaskForAudit() { $('auditTaskId').value = currentTaskId; loadAuditLogs(true); };
function syncEvaluationPromptOptions() {
  const selected = $('evalPrompt').value;
  $('evalPrompt').innerHTML = '<option value="">当前启用版本</option>' + latestPrompts.filter(p => ['relevance_agent','lead_signal_agent','critic_qa_agent','report_agent'].includes(p.agentKey)).map(p => `<option value="${Number(p.id)}">${esc(agentLabel(p.agentKey))} · ${esc(promptIterationLabel(p))} · ${esc(promptStatusLabel(p.status))}</option>`).join('');
  if (selected) $('evalPrompt').value = selected;
}
async function loadPrompts() {
  const data = await get('/api/prompts');
  latestPrompts = data.prompts || []; renderPrompts(latestPrompts);
  syncEvaluationPromptOptions();
  renderTaskInsights();
  const route = WorkspaceUI.route('runs');
  if (route.page === 'versions' && route.promptId) openCandidateDetail(route.promptId, false);
}
function renderPrompts(prompts) {
  $('prompts').innerHTML = prompts.length ? `<div class="admin-table-scroll"><table class="workspace-table admin-table"><thead><tr><th>处理环节 / 版本</th><th>状态</th><th>来源任务</th><th>变更说明</th><th></th></tr></thead><tbody>${prompts.map(p => `<tr><td><button class="admin-text-button admin-version-button" onclick="openCandidateDetail(${Number(p.id)})"><strong>${esc(agentLabel(p.agentKey || p.agentName))}</strong><span class="small">${esc(promptIterationLabel(p))} · ${esc(p.version || '')}</span></button></td><td><span class="pill ${esc(safeClass(p.status))}">${esc(promptStatusLabel(p.status))}</span></td><td>${p.sourceTaskId ? `#${fmt(p.sourceTaskId)}` : '初始版本'}</td><td class="small">${esc(promptNotesLabel(p.notes) || '—')}</td><td><button onclick="openCandidateDetail(${Number(p.id)})">查看详情</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="workspace-empty">暂无版本。</div>';
}
function promptTextDiff(before, after) {
  const oldLines = String(before || '').split('\n'), newLines = String(after || '').split('\n');
  // Ordered prefix/suffix comparison keeps repeated lines meaningful without an extra dependency.
  let first = 0; while (first < oldLines.length && first < newLines.length && oldLines[first] === newLines[first]) first++;
  let oldEnd = oldLines.length, newEnd = newLines.length;
  while (oldEnd > first && newEnd > first && oldLines[oldEnd - 1] === newLines[newEnd - 1]) { oldEnd--; newEnd--; }
  if (first === oldLines.length && first === newLines.length) return '<p class="small">与当前版本正文相同。</p>';
  return `<div class="admin-diff-grid"><section><h4>当前版本 · 变更前</h4><pre>${esc(oldLines.slice(first,oldEnd).join('\n') || '（此处没有原文）')}</pre></section><section><h4>候选版本 · 变更后</h4><pre>${esc(newLines.slice(first,newEnd).join('\n') || '（删除此段）')}</pre></section></div>`;
}
function openCandidateDetail(promptId, updateRoute = true) {
  const prompt = latestPrompts.find(p => String(p.id) === String(promptId));
  if (!prompt) { if (updateRoute) adminNotice('未找到此版本。', 'error'); return; }
  const selectedProof = adminSelectedPromptId === String(promptId) ? $('promoteEvalRun')?.value : '';
  adminSelectedPromptId = String(promptId);
  if (updateRoute) WorkspaceUI.navigate('versions', {taskId:currentTaskId || undefined,promptId:promptId});
  activateAdminPanel('agents');
  const active = latestPrompts.find(p => p.agentKey === prompt.agentKey && p.status === 'active');
  $('candidateTitle').textContent = `${agentLabel(prompt.agentKey)} · ${promptIterationLabel(prompt)}`;
  $('candidateBody').innerHTML = `<div class="admin-candidate-meta"><span class="pill ${safeClass(prompt.status)}">${esc(promptStatusLabel(prompt.status))}</span><span class="small">内部版本 ${esc(prompt.version || '-')} · ${prompt.sourceTaskId ? `来源任务 #${fmt(prompt.sourceTaskId)}` : '初始版本'}</span></div><p>${esc(promptNotesLabel(prompt.notes) || '此版本没有补充说明。')}</p>${prompt.status === 'candidate' && active ? promptTextDiff(active.prompt,prompt.prompt) : ''}<details class="admin-raw"><summary>查看完整提示词</summary><pre>${esc(prompt.prompt || '未保存正文')}</pre></details><section class="admin-candidate-proof"><h3>关联评测与启用</h3><div id="candidateProofList"></div>${prompt.status === 'candidate' ? `<div class="btn-row"><button onclick="evaluateCandidate(${Number(prompt.id)})" ${['relevance_agent','lead_signal_agent','critic_qa_agent','report_agent'].includes(prompt.agentKey) ? '' : 'disabled title="此处理环节需要专用验收集"'}>${['relevance_agent','lead_signal_agent','critic_qa_agent','report_agent'].includes(prompt.agentKey) ? '为此候选创建评测' : '此环节需要专用验收集'}</button></div><label for="promoteEvalRun">已通过的冻结验收记录</label><select id="promoteEvalRun"></select><p id="candidateLockReason" class="small"></p>` : `<p class="small">${prompt.status === 'active' ? '此版本正在使用。启用其他候选须完成匹配的冻结验收。' : '此版本未在使用；曾实际启用的版本可在下方明确回退。'}</p>`}</section>${prompt.status === 'candidate' ? `<div class="admin-dialog-footer"><button class="danger" id="candidateDisableBtn" onclick="disablePrompt(${Number(prompt.id)})">${esc(disableLabel(prompt.status))}</button>${prompt.status === 'candidate' ? `<button id="candidatePromoteBtn" class="primary" onclick="promotePrompt(${Number(prompt.id)})">确认启用此版本</button>` : ''}</div>` : ''}`;
  if (prompt.status !== 'active' && prompt.activatedAt != null) $('candidateBody').innerHTML += `<div class="admin-dialog-footer"><p class="small">此版本曾于 ${formatTime(prompt.activatedAt)} 实际启用，可明确选择作为回退目标。</p><button id="candidateRollbackBtn" onclick="rollbackPrompt(${Number(prompt.id)})">回退到此历史版本</button></div>`;
  if (prompt.status === 'active') {
    const targets = latestPrompts.filter(item => item.agentKey === prompt.agentKey && item.id !== prompt.id && item.activatedAt != null);
    $('candidateBody').innerHTML += `<section class="admin-candidate-proof"><h3>回退到历史版本</h3>${targets.length ? targets.map(item => `<button onclick="openCandidateDetail(${Number(item.id)})">查看 ${esc(promptIterationLabel(item))} · #${Number(item.id)}</button>`).join(' ') : '<p class="small">没有曾实际启用的历史版本可回退。</p>'}</section>`;
  }
  refreshCandidateProof(selectedProof);
  if (!$('candidateDialog').open) WorkspaceUI.showDialog($('candidateDialog'));
}
function refreshCandidateProof(selectedValue) {
  if (!$('candidateProofList')) return;
  const runs = evaluationRuns.filter(r => String(r.promptId || r.prompt_id) === adminSelectedPromptId);
  $('candidateProofList').innerHTML = runs.length ? runs.map(run => `<div class="admin-proof-row"><div><b>${esc(evaluationState(run))}</b><span class="small">${esc(String(run.runId || run.id).slice(0,8))} · ${run.split === 'acceptance' ? '冻结验收' : '调试集'} · ${formatTime(run.createdAt)}</span></div><button onclick="openCandidateEvaluation('${esc(run.runId || run.id)}')">查看输出</button></div>`).join('') : '<div class="workspace-empty">此候选尚无评测记录。</div>';
  const select = $('promoteEvalRun');
  if (select) {
    const previous = selectedValue || select.value;
    const passed = runs.filter(evaluationEligibleForPromotion);
    select.innerHTML = '<option value="">选择对应验收记录</option>' + passed.map(run => `<option value="${esc(run.runId || run.id)}">${esc(String(run.runId || run.id).slice(0,8))} · ${formatTime(run.createdAt)} · ${fmt(run.rows?.length || 0)} 条</option>`).join('');
    if (previous) select.value = previous;
    const running = hasAdminRunningTask();
    $('candidatePromoteBtn').disabled = running || !passed.length || (typeof promotionBusy !== 'undefined' && promotionBusy);
    $('candidateLockReason').textContent = running ? '任务运行期间不能切换版本。完成或停止后刷新。' : passed.length ? '选择匹配的记录后仍需明确确认；后端会再次校验。' : '没有最终通过的冻结验收记录，暂不可启用。';
  }
  if ($('candidateDisableBtn')) $('candidateDisableBtn').disabled = hasAdminRunningTask() || adminDisablePromptBusy;
  if ($('candidateRollbackBtn')) $('candidateRollbackBtn').disabled = hasAdminRunningTask() || adminRollbackBusy;
}
function closeCandidateDetail() {
  adminSelectedPromptId = ''; WorkspaceUI.closeDialog($('candidateDialog'));
  const route = WorkspaceUI.route('runs'); if (route.promptId) adminRouteTo('versions', {}, true);
}
function evaluateCandidate(promptId) {
  closeCandidateDetail(); setActivePage('evaluations'); syncEvaluationPromptOptions(); $('evalPrompt').value = String(promptId);
  if ($('evalPrompt').value !== String(promptId)) adminNotice('此处理环节需要专用验收集，请选择该处理环节对应的专用样本集。', 'error');
  $('evalDataset').focus();
}
async function openCandidateEvaluation(runId) {
  closeCandidateDetail(); setActivePage('evaluations'); $('evalRunSelect').value = runId; await selectEvaluationRun();
}
let adminDisablePromptBusy = false;
let adminRollbackBusy = false;
async function rollbackPrompt(promptId) {
  const prompt = latestPrompts.find(item => Number(item.id) === Number(promptId));
  if (adminRollbackBusy || !prompt || prompt.status === 'active' || prompt.activatedAt == null) return adminNotice('只能回退到曾实际启用的历史版本。');
  if (!await adminMutationAllowed()) return;
  const active = latestPrompts.find(item => item.agentKey === prompt.agentKey && item.status === 'active');
  if (!await WorkspaceUI.confirm({title:'确认历史版本回退',message:`将 ${agentLabel(prompt.agentKey)} 从 ${active?.version || '当前版本'}（#${active?.id || '-'}）回退到 ${prompt.version || promptIterationLabel(prompt)}（#${prompt.id}）。后续新任务使用所选历史版本。`,confirmText:'确认回退',danger:true})) return;
  adminRollbackBusy = true; refreshCandidateProof();
  try {
    await post('/api/prompts/rollback',{promptId:Number(prompt.id),agentKey:prompt.agentKey}); await loadPrompts();
    if (currentTaskId) await loadTask(currentTaskId);
    if (adminSelectedPromptId === String(promptId)) openCandidateDetail(promptId,false);
    adminNotice('已回退到指定历史版本，后续新任务使用该版本。','success');
  } catch (error) { adminNotice(`回退未完成：${DisplayText.message(error.message)}`,'error'); }
  finally { adminRollbackBusy = false; refreshCandidateProof(); }
}
async function disablePrompt(promptId) {
  if (adminDisablePromptBusy) return;
  adminDisablePromptBusy = true;
  const button = $('candidateDisableBtn');
  if (button) button.disabled = true;
  try {
    if (!await adminMutationAllowed()) return;
    const prompt = latestPrompts.find(p => Number(p.id) === Number(promptId));
    if (!await WorkspaceUI.confirm({title:prompt?.status === 'active' ? '回退当前版本' : '禁用候选版本',message:prompt?.status === 'active' ? '将停用当前版本并按现有规则恢复可用版本。确认继续？' : '此候选将不再可启用。确认禁用？',confirmText:prompt?.status === 'active' ? '确认回退' : '确认禁用',danger:true})) return;
    if (button?.isConnected) button.textContent = '正在处理…';
    await post('/api/prompts/disable',{promptId}); await loadPrompts();
    if (currentTaskId) await loadTask(currentTaskId);
    if (adminSelectedPromptId === String(promptId)) openCandidateDetail(promptId,false);
    adminNotice('版本状态已更新。','success');
  } catch (error) { adminNotice(`操作失败：${DisplayText.message(error.message)}`,'error'); }
  finally {
    adminDisablePromptBusy = false;
    if (button?.isConnected) { const prompt = latestPrompts.find(p => Number(p.id) === Number(promptId)); button.textContent = disableLabel(prompt?.status); }
    refreshCandidateProof();
  }
}
async function showAgentPrompts(agentKey) {
  const prompt = latestPrompts.find(p => p.agentKey === agentKey && p.status === 'active') || latestPrompts.find(p => p.agentKey === agentKey);
  if (prompt) openCandidateDetail(prompt.id); else adminNotice('此处理环节尚无版本。');
}
function hasAdminRunningTask() { return Boolean(latestTask?.isRunning || latestTask?.isEvolving || adminReflectionTaskId || adminTasks.some(t => t.status === 'running' || t.isEvolving)); }
async function adminMutationAllowed() {
  try { await loadHistory(); }
  catch (error) { adminNotice(`无法确认运行状态：${DisplayText.message(error.message)}。请刷新后重试。`,'error'); return false; }
  if (hasAdminRunningTask()) { adminNotice('任务执行或复盘期间不能修改设置或切换提示词。请结束后重试。','error'); return false; }
  return true;
}
function adminSettingsFields() {
  return Array.from(document.querySelectorAll('#page-settings input, #page-settings select')).map(el => [el.id,el.type === 'checkbox' ? el.checked : el.value]);
}
function adminSettingsFingerprint(fields = adminSettingsFields()) {
  return JSON.stringify(fields);
}
function markAdminSettingsDirty() {
  if (!settingsLoaded) return;
  adminSettingsDirty = adminSettingsFingerprint() !== adminSettingsSnapshot;
  document.querySelectorAll('#page-settings [aria-invalid]').forEach(el => el.removeAttribute('aria-invalid'));
  updateAdminSettingsState();
}
function updateAdminSettingsState() {
  const running = hasAdminRunningTask();
  $('settingsDirtyStatus').textContent = adminSettingsDirty ? '有未保存更改' : '所有更改已保存';
  $('settingsDirtyStatus').classList.toggle('admin-dirty',adminSettingsDirty);
  $('discardSettingsBtn').disabled = !adminSettingsDirty || adminSettingsSaving;
  $('nav-settings').classList.toggle('has-draft',adminSettingsDirty);
  for (const id of ['settingsModelGroup','settingsPlatformGroup','settingsSafetyGroup']) $(id).disabled = running || adminSettingsSaving;
  $('saveSettingsBtn').disabled = running || adminSettingsSaving;
  if (running) { $('settingsNotice').textContent = '当前有任务运行，模型、平台和安全设置暂时锁定。草稿已保留，任务结束后可继续编辑。'; $('settingsNotice').dataset.tone = 'warning'; }
  else if ($('settingsNotice').dataset.tone === 'warning') { $('settingsNotice').textContent = ''; $('settingsNotice').dataset.tone = ''; }
  refreshCandidateProof();
}
function loadSettings(force = false) {
  if (adminSettingsDirty || adminSettingsLoading) return adminSettingsLoading || Promise.resolve();
  if (settingsLoaded && !force) return Promise.resolve();
  adminSettingsLoading = readSettingsIntoForm().then(() => {
    if (settingsLoaded) { adminSettingsSnapshot = adminSettingsFingerprint(); adminSettingsDirty = false; updateAdminSettingsState(); }
  }).finally(() => { adminSettingsLoading = null; });
  return adminSettingsLoading;
}
async function saveSettings() {
  if (adminSettingsSaving) return;
  adminSettingsSaving = true; updateAdminSettingsState();
  const button = $('saveSettingsBtn'), oldText = button.textContent;
  button.textContent = '正在保存…';
  try {
    if (!await adminMutationAllowed()) return;
    $('settingsNotice').textContent = ''; $('settingsNotice').dataset.tone = '';
    for (const id of ['settingConnectTimeout','settingReadTimeout','settingTotalTimeout']) {
      const field = $(id), value = Number(field.value);
      if (!field.value.trim() || !Number.isInteger(value) || value < 1 || value > 86400) { field.setAttribute('aria-invalid','true'); field.focus(); break; }
    }
    const body = readSettingsForm(), submittedFields = adminSettingsFields();
    const submittedKey = $('settingApiKey').value;
    await post('/api/settings', body);
    settingsLoaded = true;
    if ($('settingApiKey').value === submittedKey) $('settingApiKey').value = '';
    // The saved baseline describes the submitted values, never newer input.
    adminSettingsSnapshot = adminSettingsFingerprint(submittedFields.map(([id, value]) => [id, id === 'settingApiKey' ? '' : value]));
    adminSettingsDirty = adminSettingsFingerprint() !== adminSettingsSnapshot;
    $('settingsNotice').textContent = adminSettingsDirty ? '本次提交已保存，之后的修改仍未保存。' : '设置已保存，密钥字段已清空；后续任务使用新配置。';
    $('settingsNotice').dataset.tone = 'success';
    adminNotice('设置已保存。', 'success');
  } catch (error) { adminNotice(`保存设置失败：${error.message || error}`, 'error'); }
  finally { adminSettingsSaving = false; button.textContent = oldText; updateAdminSettingsState(); }
}
async function discardAdminSettings() {
  if (!adminSettingsDirty || adminSettingsSaving) return;
  if (!await WorkspaceUI.confirm({title:'放弃未保存更改',message:'将恢复上次保存的设置，当前输入的密钥也会清空。',confirmText:'放弃更改'})) return;
  adminSettingsDirty = false; settingsLoaded = false; await loadSettings(true);
  if (!settingsLoaded) { adminSettingsDirty = true; updateAdminSettingsState(); }
  else { $('settingsNotice').textContent = '已恢复上次保存的设置。'; $('settingsNotice').dataset.tone = 'success'; }
}
function renderFeedback(items) {
  const filter = $('adminFeedbackFilter').value;
  const visible = items.filter(item => !filter || item.status === filter);
  renderFeedbackItems(visible);
  // Retain evidence and actions, but hide diagnostic JSON until requested.
  $('feedbackQueue').querySelectorAll('.feedback-output').forEach(output => {
    const details = document.createElement('details'); details.className = 'admin-raw';
    const summary = document.createElement('summary'); summary.textContent = '证据与分析详情';
    output.before(details); details.append(summary,output);
  });
}
function feedbackStatusLabel(status) { return {pending:'待分析',analyzed:'待创建候选',applied:'已创建候选',dismissed:'已忽略'}[status] || DisplayText.status(status); };
async function applyFeedback(feedbackId) {
  if (busyFeedback.has(Number(feedbackId))) return;
  setFeedbackBusy(feedbackId,'正在保存候选版本…');
  try {
    const result = await post('/api/feedback/apply',{feedbackId}); clearFeedbackBusy(feedbackId);
    await Promise.all([loadFeedback(),loadPrompts()]);
    if (result.candidatePromptId) openCandidateDetail(result.candidatePromptId);
    adminNotice('候选已创建，评测通过后可手动启用。','success');
  } catch (error) { setFeedbackError(feedbackId,`创建失败：${DisplayText.message(error.message)}`); }
}
function showAdminGate(message = '请先登录。') {
  ++adminTaskGeneration; ++adminAuditGeneration; ++adminHistoryGeneration;
  adminPendingTask = ''; clearTimeout(adminPollTimer); adminPollTimer = null;
  adminReady = false;
  adminSetupMode = false;
  $('adminGateTitle').textContent = '管理员登录';
  $('adminGateIntro').textContent = '输入本机管理员账号与密码，查看任务运行和质量评测。';
  $('adminPasswordLabel').textContent = '管理密码';
  $('setupConfirmFields').style.display = 'none';
  $('adminLoginBtn').style.display = 'inline-flex';
  $('adminSetupBtn').style.display = 'none';
  $('adminGate').style.display = 'grid';
  $('harnessMain').style.display = 'none';
  $('adminNav').style.display = 'none';
  $('adminLoginStatus').textContent = message;
  $('adminLoginStatus').style.color = 'var(--muted)';
}
async function adminLogout() {
  if (adminSettingsDirty && !await WorkspaceUI.confirm({title:'退出登录',message:'设置有未保存更改。确认退出？',confirmText:'退出'})) return;
  try {
    await fetch(`${apiBase()}/api/auth/logout`, { method: 'POST', credentials: 'same-origin' });
  } catch {}
  showAdminGate('已退出管理后台。');
}
window.addEventListener('hashchange', applyAdminRoute);
document.addEventListener('keydown', event => { if (event.key === 'Escape' && !event.defaultPrevented && $('harnessMain').classList.contains('nav-open')) { event.preventDefault(); toggleAdminNav(false); $('adminMenuButton').focus(); } });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearTimeout(adminPollTimer); adminPollTimer = null; }
  else if (adminReady && currentTaskId) loadTask(currentTaskId);
});
window.addEventListener('beforeunload', event => {
  const note = $('evalHumanNote')?.value.trim() || '';
  const reviewDraft = Boolean(note !== (currentEvaluationResult?.humanReview?.note || '').trim() || (!currentEvaluationResult?.humanReviewComplete && (evaluationReviewed.size || ['evalReviewedAll','evalNoFabrication','evalCitationsVerified'].some(id => $(id)?.checked))));
  if (adminSettingsDirty || reviewDraft) { event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('DOMContentLoaded', () => {
  for (const [id,name] of [['nav-overview','activity'],['nav-evaluations','review'],['nav-agents','versions'],['nav-feedback','feedback'],['nav-settings','settings']]) {
    const button = $(id), glyph = button.querySelector('span'); if (glyph) glyph.outerHTML = WorkspaceUI.icon(name);
  }
  document.querySelector('.brand-mark').innerHTML = WorkspaceUI.icon('logo');
  if ($('adminGateBrandIcon')) $('adminGateBrandIcon').innerHTML = WorkspaceUI.icon('logo');
  $('page-settings').addEventListener('input',markAdminSettingsDirty); $('page-settings').addEventListener('change',markAdminSettingsDirty);
  $('auditTaskId').readOnly = true; $('auditTaskId').title = '任务由顶部任务上下文决定';
  $('candidateDialog').addEventListener('close', () => {
    const route = WorkspaceUI.route('runs');
    if (route.promptId && !adminSelectedPromptId) WorkspaceUI.navigate('versions',{taskId:currentTaskId || undefined},true);
  });
  $('adminMenuButton').innerHTML = WorkspaceUI.icon('menu');
  window.addEventListener('resize', () => toggleAdminNav(false));
  applyAdminRoute();
});
