/* Keyword workbench: confirmed plans, independent reviews and single-action outreach. */
'use strict';
let activeSearchPlan = null;
let exportScope = 'approved';
let outreachContext = null;
let preparedOutreach = null;
let outreachBusy = false;
let outreachSubmitted = false;
let outreachDraftGeneration = 0;
let authenticated = false;
let taskTemplates = [];
let renderedTaskId = null;
let currentTaskSnapshot = null;
let leadViewFilter = 'all';
let leadFilterContext = '';
let resultFormDrafts = {};
const FOCUSED_OFFERING = 'agent_custom_development';
const OFFERING_GOALS = {
  agent_custom_development: '寻找正在寻求他人提供智能体定制开发、外包交付或报价的真实需求；排除仅学习、求教程、工具使用和服务商宣传。',
  general: '寻找与关键词相关的真实需求和讨论'
};
const STAGE_LABELS = WorkspaceData.stages;
const ACTION_LABELS = { follow: '关注', comment: '发表评论', reply_comment: '回复评论', send_dm: '发送私信' };
const LIMIT_FIELDS = { maxKeywords: 'maxKeywords', maxPagesPerKeyword: 'maxPagesPerKeyword', maxNotes: 'count', maxCommentsPerNote: 'maxCommentsPerNote', maxCommentsTotal: 'maxCommentsTotal' };
function notifyWorkbench(message, tone = 'error') { const text = systemText(message); if (window.WorkspaceUI) window.WorkspaceUI.notify(text, tone); else alert(text); }
async function promptWorkbench(title, label, value = '', options = {}) { return window.WorkspaceUI ? window.WorkspaceUI.prompt({ ...options, title, label, value }) : prompt(label, value); }

function safeUrl(value) {
  try {
    const url = new URL(String(value || ''), location.origin);
    if (!/^https?:$/.test(url.protocol)) return '#';
    url.username = ''; url.password = '';
    for (const key of [...url.searchParams.keys()]) if (credentialField(key)) url.searchParams.delete(key);
    // Fragments can carry implicit-login tokens; source identifiers in the path
    // and non-credential query fields are retained.
    url.hash = '';
    return url.href;
  } catch { return '#'; }
}
function credentialField(value) { return /^(?:cookie|cookies|setcookie|authorization|proxyauthorization|apikey|xapikey|key|password|passwd|secret|clientsecret|token|accesstoken|refreshtoken|authtoken|sessiontoken|sessionid|xsectoken)$/i.test(String(value).replace(/[-_]/g, '')); }
function splitKeywords(value) {
  return [...new Set((Array.isArray(value) ? value : String(value || '').split(/[,，\n;；]+/)).map(v => String(v).trim()).filter(Boolean))];
}
function demandLabel(value) {
  return ({ demand: '明确需求', uncertain: '待确认需求', information: '经验或知识讨论', explicit_demand: '明确需求', clear_demand: '明确需求', uncertain_demand: '待确认需求', needs_confirmation: '待确认需求', discussion: '经验或知识讨论', knowledge_discussion: '经验或知识讨论', promotion: '供方宣传', supplier_promotion: '供方宣传', irrelevant: '无关内容' })[value] || window.DisplayText?.classification(value) || systemCode(value, '待确认需求');
}
function normalizedOffering(value) { return value === FOCUSED_OFFERING ? FOCUSED_OFFERING : 'general'; }
function offeringLabel(value) { return normalizedOffering(value) === FOCUSED_OFFERING ? '智能体定制开发' : '通用关键词研究'; }
function renderOfferingContext() {
  const focused = normalizedOffering($('offeringId').value) === FOCUSED_OFFERING;
  $('offeringContext').textContent = focused
    ? '当前服务：智能体定制开发。寻找买方委托、外包交付和询价的原文证据，学习讨论、工具使用和服务商宣传分开查看。'
    : '当前模式：通用关键词研究。按你填写的目标分析相关内容，不套用智能体定制开发需求条件。';
}
function changeOffering() {
  const offering = normalizedOffering($('offeringId').value);
  const goal = $('demandDescription').value.trim();
  // Only replace our untouched defaults when the user explicitly changes mode.
  if (!goal || Object.values(OFFERING_GOALS).includes(goal)) $('demandDescription').value = OFFERING_GOALS[offering];
  renderOfferingContext(); invalidateSearchPlan();
}
function matchingTaskSnapshot(data) {
  const resultTaskId = data?.taskId ?? currentTaskId;
  return String(currentTaskSnapshot?.taskId ?? currentTaskSnapshot?.id ?? '') === String(resultTaskId || '') ? currentTaskSnapshot : null;
}
function resultOfferingId(data) {
  return normalizedOffering(data?.offeringId ?? data?.confirmedPlan?.offeringId ?? data?.plan?.offeringId ?? matchingTaskSnapshot(data)?.confirmedPlan?.offeringId);
}
function intentRoleLabel(value) { return ({ buyer: '买方寻求服务', supplier: '服务商供给', discussion: '学习或经验讨论', unclear: '角色待确认' })[value] || '角色未记录'; }
function serviceFitLabel(value) { return ({ custom_development: '匹配定制开发', not_fit: '不匹配定制开发', unclear: '匹配度待确认' })[value] || '匹配度未记录'; }
function demandOriginLabel(value) { return ({ self: '本人需求', third_party: '转述他人需求', unclear: '来源待确认' })[value] || '来源未记录'; }
function isQualifiedLeadForDisplay(lead) { return lead?.qualified === true && (!lead.qa_status || lead.qa_status === 'approved'); }
function qualificationDisplayLabel(lead) {
  if ((lead.qualified === true || lead.preQaQualified === true) && lead.qa_status && lead.qa_status !== 'approved') return '研判初步符合，质检待核验';
  if (isQualifiedLeadForDisplay(lead)) return lead.qa_status === 'approved' ? '合格定制需求（仍需人工核验）' : '已记录需求资格（质检未记录，需核验）';
  return '尚未满足合格定制需求条件';
}
function isUncertainLead(lead) { return lead.classification === 'uncertain' || (lead.classification === 'demand' && !isQualifiedLeadForDisplay(lead)); }
function initializeLeadFilter(data) {
  const context = `${data?.taskId ?? currentTaskId ?? ''}:${resultOfferingId(data)}`;
  if (leadFilterContext !== context) {
    leadFilterContext = context;
    leadViewFilter = resultOfferingId(data) === FOCUSED_OFFERING ? 'pending' : 'all';
  }
}
function filteredLeadEntries(data = cachedData) {
  // Carry the original array index through filtering; actions always use cachedData.
  return (data?.leads?.leads || []).map((lead, index) => ({ lead, index })).filter(({ lead }) => {
    if (leadViewFilter === 'pending') return isQualifiedLeadForDisplay(lead) || isUncertainLead(lead);
    if (leadViewFilter === 'qualified') return isQualifiedLeadForDisplay(lead);
    if (leadViewFilter === 'uncertain') return isUncertainLead(lead);
    return leadViewFilter === 'all' || lead.classification === leadViewFilter;
  });
}
function changeLeadFilter(value) {
  if (!['pending', 'qualified', 'uncertain', 'all', 'demand', 'information', 'promotion', 'irrelevant'].includes(value)) return;
  leadViewFilter = value;
  if (cachedData) render(cachedData);
}
function leadFilterControls(focused, shown, total) {
  if (!focused) return `<div class="small">本任务按通用关键词研究展示全部 ${fmt(total)} 条候选。</div>`;
  return `<label for="leadFilter">需求筛选</label><select id="leadFilter" onchange="changeLeadFilter(this.value)">
    <option value="pending">合格与待确认需求</option>
    <option value="qualified">合格定制需求</option><option value="uncertain">待确认需求（含尚未合格的明确需求）</option>
    <option value="all">全部分析结果（含讨论与宣传）</option><option value="demand">全部明确需求</option>
    <option value="information">经验或知识讨论</option><option value="promotion">供方宣传</option><option value="irrelevant">无关内容</option></select>
    <p class="small">显示 ${fmt(shown)} / ${fmt(total)} 条。合格需求须具备买方委托意图与原文证据；资格判断不等于人工审核通过。</p>`;
}
function emptyLeadMessage(focused) {
  if (focused && leadViewFilter === 'pending') return '当前没有合格或待确认需求。可查看全部分类与任务说明；不会用学习讨论或服务商宣传填充结果。';
  if (focused && leadViewFilter === 'qualified') return '没有发现已满足条件的智能体定制开发需求。可查看待确认项、全部分类与任务说明；不会用学习闲聊或服务商宣传填充合格结果。';
  if (focused && leadViewFilter === 'uncertain') return '本次没有待确认需求。请结合任务说明判断已完成的分析范围。';
  return '当前范围没有结果。请查看来源内容、其他分类和任务说明。';
}
function syncTaskStatus(data, prefix = '') {
  // Completed history supplies background context for a fresh task.
  // Keep errors, active planning and running locks visible if they arrived first.
  if (data.status === 'completed' && !data.isRunning && window.WorkspaceUI?.route('tasks')?.page === 'new') {
    const completedRunningTask = $('status').dataset.taskStatus === 'running' && $('searchBtn').textContent === '任务运行中';
    if (completedRunningTask || (!activeSearchPlan && !$('searchBtn').disabled && !/\bstatus-(error|running)\b/.test($('status').className))) setStatus('尚未开始任务。确认搜索计划后才会开始采集。');
    return;
  }
  const label = taskStatusLabel(data.status);
  const round = data.totalRounds ? `第 ${data.currentRound || 0}/${data.totalRounds} 轮 · ` : '';
  const explanation = window.TaskInsights?.execution(data).detail || data.statusText;
  const detail = explanation && explanation !== label ? ` · ${systemText(explanation)}` : '';
  const loginHelp = !data.isRunning && needsXhsLoginAssist(data.errorText || data.statusText);
  const message = `${prefix}${round}${label}${detail}${loginHelp ? '。可点右上角使用“小红书登录/验证助手”。' : ''}`;
  const icon = loginHelp || data.status === 'failed' ? '❌' : data.isRunning ? '⏳' : data.status === 'completed' ? '✅' : '⚠️';
  setStatus(message, icon);
  $('status').dataset.taskStatus = data.status || '';
}
function stageCoverageHtml(data = {}, task = matchingTaskSnapshot(data) || {}) {
  const coverage = data.stageCoverage || task.stageCoverage;
  const scope = data.stageCoverageScope || task.stageCoverageScope;
  const rounds = data.roundStageCoverage || task.roundStageCoverage;
  const scopeHint = scope === 'executed_rounds'
    ? `<p class="small"><b>批量覆盖：按已执行轮次累计</b>${Array.isArray(rounds) ? `（当前记录 ${fmt(rounds.length)} 轮）` : ''}。查询组合在各轮分别计数，不能与单轮计划数量直接比较。</p>`
    : '';
  const labels = WorkspaceData.coverageStatuses;
  if (coverage && typeof coverage === 'object' && !Array.isArray(coverage)) {
    return '<b>阶段覆盖</b>' + scopeHint + '<div class="report-grid" style="margin-top:8px;">' + Object.entries(STAGE_LABELS).map(([key, label]) => {
      const row = coverage[key] || {}, view = window.TaskInsights?.stageView(row), status = view?.label || labels[row.status] || '完成情况未记录';
      const hasCounts = view ? view.showCounts : Number.isInteger(row.completed) && row.completed >= 0 && Number.isInteger(row.total) && row.total >= 0;
      const reason = view?.reason || row.reason;
      const unit = WorkspaceData.coverageUnits[row.unit] || '项（单位未记录）';
      return `<div class="small"><b>${esc(label)}：${esc(status)}</b>${hasCounts ? ` · ${fmt(row.completed)} / ${fmt(row.total)} ${esc(unit)}` : ''}${reason ? `<div>${esc(systemText(reason))}</div>` : ''}${row.error ? `<div>${esc(systemText(row.error))}${systemText(row.error) !== row.error ? technicalDetails(row.error) : ''}</div>` : ''}</div>`;
    }).join('') + '</div>';
  }
  const notes = data.notes || [], leadCount = data.leads?.leads?.length || 0;
  const comments = data.leads?.totalCommentsScanned ?? data.scannedCommentsCount ?? data.collectionSummary?.commentsCollected;
  const hasQa = data.qa && typeof data.qa === 'object' && Object.keys(data.qa).length > 0;
  const hasReport = data.report && typeof data.report === 'object' && Object.keys(data.report).length > 0;
  const evidence = {
    search: notes.length ? `已保存 ${notes.length} 篇笔记；完整性未记录` : '未记录阶段完成情况',
    relevance: '未记录阶段完成情况',
    details: '未记录阶段完成情况',
    comments: Number.isInteger(comments) && comments > 0 ? `已记录 ${comments} 条评论计数；完整性未记录` : '未记录阶段完成情况',
    lead_signal: leadCount ? `已保存 ${leadCount} 条候选；完整性未记录` : '未记录阶段完成情况',
    qa: hasQa ? '已有质检产物；完整性未记录' : '未见已保存的质检产物，未确认完成',
    report: hasReport ? '已有报告产物；完整性未记录' : '未见已保存的报告，未确认完成'
  };
  return '<b>阶段覆盖未记录</b><p class="small">此任务没有逐阶段完成记录。以下仅说明已保存的产物，不能据此认定全部分析完成。</p><div class="report-grid">' +
    Object.entries(STAGE_LABELS).map(([key, label]) => `<div class="small"><b>${esc(label)}</b>：${esc(evidence[key])}</div>`).join('') + '</div>';
}
function reviewLabel(value) { return ({ approved: '人工通过', rejected: '人工驳回', pending: '待人工审核' })[value] || '待人工审核'; }
function reviewOptions(value) {
  return ['pending', 'approved', 'rejected'].map(s => `<option value="${s}" ${s === (value || 'pending') ? 'selected' : ''}>${reviewLabel(s)}</option>`).join('');
}
function showLoginGate(message = '请输入本机管理员账号与密码。') {
  authenticated = false;
  clearInterval(pollTimer); pollTimer = null;
  if (typeof stopTaskPolling === 'function') stopTaskPolling();
  stopXhsLoginPolling(); stopDouyinLoginPolling();
  $('salesMain').style.display = 'none'; $('salesNav').style.display = 'none'; $('taskHistory').style.display = 'none';
  $('outreachHistoryPanel').style.display = 'none';
  $('workbenchGate').style.display = 'block'; setSystemMessage('workbenchLoginStatus', message);
  $('workbenchLoginStatus').style.color = 'var(--muted)';
  for (const id of ['xhsLoginModal', 'douyinLoginModal', 'dmDraftModal']) $(id).style.display = 'none';
  window.WorkbenchWorkspace?.authChanged(false);
}
async function apiFetch(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  if (response.status === 401) {
    showLoginGate('会话已失效，任务轮询已暂停。登录后可恢复查看，触达动作不会自动重发。');
    throw new Error('请重新登录本地工作台');
  }
  return response;
}
function apiGet(path) {
  return WorkspaceData.requestJson(`${apiBase()}${path}`, { fetcher: apiFetch });
}
async function enterWorkbench(user) {
  authenticated = true;
  document.body?.classList.remove('wb-signed-out');
  $('workbenchGate').style.display = 'none'; showSalesApp();
  $('salesUserLabel').textContent = user?.username || '本机管理员';
  await loadConfig(); await restoreTaskDefaults(); await renderTemplates();
  $('outreachHistoryPanel').style.display = 'block'; await loadOutreachHistory();
  await restoreLastTask();
  if (sessionStorage.getItem('keywordWorkbenchLastAction')) $('recoverOutreachBtn').style.display = 'inline-flex';
  window.WorkbenchWorkspace?.authChanged(true);
}
async function bootSales() {
  showLoginGate('正在检查本地会话…');
  try {
    const response = await fetch(`${apiBase()}/api/auth/me`, { credentials: 'same-origin' });
    const result = await response.json();
    if (!response.ok || !result.ok || !result.data?.user) return showLoginGate();
    await enterWorkbench(result.data.user);
  } catch (error) { showLoginGate(`无法连接工作台：${error.message}。请确认本地服务正在运行。`); }
}
async function loginWorkbench() {
  const button = $('workbenchLoginBtn'); if (button.disabled) return;
  const label = button.textContent;
  button.disabled = true; button.textContent = '正在登录…';
  $('workbenchLoginStatus').textContent = '正在验证账号与密码…';
  $('workbenchLoginStatus').style.color = 'var(--muted)';
  try {
    const response = await fetch(`${apiBase()}/api/auth/login`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: $('workbenchUsername').value.trim() || 'admin', password: $('workbenchPassword').value, role: 'admin' }) });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || '登录失败');
    $('workbenchPassword').value = ''; await enterWorkbench(result.data?.user);
  } catch (error) {
    if (authenticated) notifyWorkbench(`已登录，但工作台数据暂时无法加载：${error.message}`);
    else { setSystemMessage('workbenchLoginStatus', error.message); $('workbenchLoginStatus').style.color = 'var(--red)'; }
  }
  finally { button.disabled = false; button.textContent = label; }
}
async function logoutWorkbench() {
  try { await postJson('/api/auth/logout', {}); } catch {}
  showLoginGate('已退出。');
}
async function loadConfig() {
  workbenchConfig = await apiGet('/api/config');
  const platforms = workbenchConfig.platforms?.enabled || ['xhs'];
  $('platformXhs').checked = platforms.includes('xhs'); $('platformDouyin').checked = platforms.includes('douyin');
}
function readNumber(id, label, { integer = true, min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const raw = $(id).value.trim(), value = Number(raw);
  if (!raw || !Number.isFinite(value) || (integer && !Number.isSafeInteger(value)) || value < min || value > max) {
    window.WorkbenchWorkspace?.revealField(id);
    $(id).focus(); throw new Error(`${label}须为${integer ? '整数' : '数字'}，范围 ${min}–${max === Number.MAX_SAFE_INTEGER ? '安全整数上限' : max}。`);
  }
  return value;
}
function payload() {
  const seedKeywords = splitKeywords($('keyword').value);
  if (!seedKeywords.length) { $('keyword').focus(); throw new Error('请至少填写一个种子关键词。'); }
  const data = {
    offeringId: normalizedOffering($('offeringId').value),
    seedKeywords, searchGoal: [$('demandDescription').value.trim() || OFFERING_GOALS[normalizedOffering($('offeringId').value)], $('industry').value.trim() ? '行业：' + $('industry').value.trim() : '', $('region').value.trim() ? '地域需求：' + $('region').value.trim() + '（依据原文语境，不按 IP 属地排除）' : ''].filter(Boolean).join('；'), excludeKeywords: splitKeywords($('excludeKeywords').value),
    platforms: [$('platformXhs').checked ? 'xhs' : '', $('platformDouyin').checked ? 'douyin' : ''].filter(Boolean),
    industry: $('industry').value.trim(), region: $('region').value.trim(), metric: $('metric').value, sort: $('sort').value,
    timeFilter: $('timeFilter').value, readDetail: $('readDetail').checked, scanLeads: $('scanLeads').checked,
    minScore: readNumber('minScore', '线索阈值', { min: 0, max: 100 }),
    delayMin: readNumber('delayMin', '最短采集间隔', { integer: false, min: 0 }), delayMax: readNumber('delayMax', '最长采集间隔', { integer: false, min: 0 })
  };
  for (const [key, id] of Object.entries(LIMIT_FIELDS)) data[key] = readNumber(id, $(id).previousElementSibling?.textContent || key);
  if (!data.platforms.length) throw new Error('请至少选择一个采集平台。');
  if (data.delayMax < data.delayMin) throw new Error('最长采集间隔不能小于最短间隔。');
  return data;
}
function applyInputs(data) {
  if (!data) return;
  if (data.offeringId !== undefined) {
    $('offeringId').value = normalizedOffering(data.offeringId);
    if (data.searchGoal === undefined && Object.values(OFFERING_GOALS).includes($('demandDescription').value.trim())) {
      $('demandDescription').value = OFFERING_GOALS[$('offeringId').value];
    }
  }
  if (data.seedKeywords) $('keyword').value = splitKeywords(data.seedKeywords).join('\n');
  if (data.searchGoal !== undefined) $('demandDescription').value = data.searchGoal;
  if (data.excludeKeywords) $('excludeKeywords').value = splitKeywords(data.excludeKeywords).join('，');
  for (const [key, id] of Object.entries(LIMIT_FIELDS)) if (data[key] !== undefined) $(id).value = data[key];
  for (const id of ['industry', 'region', 'metric', 'sort', 'timeFilter', 'delayMin', 'delayMax', 'minScore']) if (data[id] !== undefined) $(id).value = data[id];
  for (const id of ['readDetail', 'scanLeads']) if (typeof data[id] === 'boolean') $(id).checked = data[id];
  if (data.platforms) { $('platformXhs').checked = data.platforms.includes('xhs'); $('platformDouyin').checked = data.platforms.includes('douyin'); }
  renderOfferingContext();
  window.WorkbenchWorkspace?.inputsChanged();
}
async function saveTaskDefaults() {
  try { const plan = payload(); const result = await postJson('/api/collection/defaults', { plan }); workbenchConfig.collectionDefaults = result || plan; setStatus('已将采集参数保存为本机默认配置。', '✅'); }
  catch (error) { notifyWorkbench(error.message); }
}
async function restoreTaskDefaults() { const defaults = { ...(workbenchConfig.collectionDefaults || {}) }; for (const key of ['seedKeywords', 'searchGoal', 'excludeKeywords', 'keywords']) delete defaults[key]; applyInputs(defaults); }
async function renderTemplates() {
  try { const result = await apiGet('/api/templates'); taskTemplates = result.templates || []; $('taskTemplate').innerHTML = '<option value="">选择模板</option>' + taskTemplates.map((item, index) => `<option value="${index}">${esc(item.name)}</option>`).join(''); window.WorkbenchWorkspace?.connectionUpdated(true); }
  catch (error) { window.WorkbenchWorkspace?.connectionUpdated(false, `模板同步失败：${error.message}`); if (!taskTemplates.length) $('taskTemplate').innerHTML = '<option value="">模板读取失败</option>'; }
}
async function saveTaskTemplate() {
  try { const plan = payload(), name = await promptWorkbench('保存任务模板', '模板名称'); if (!name?.trim()) return; await postJson('/api/templates', { name: name.trim(), plan }); await renderTemplates(); setStatus('任务模板已保存到本机数据库。', '✅'); window.WorkbenchWorkspace?.templatesUpdated(); }
  catch (error) { notifyWorkbench(error.message); }
}
function loadTaskTemplate() { const value = $('taskTemplate').value; if (value === '') return; const template = taskTemplates[Number(value)]; if (template) { applyInputs({ ...template.plan, offeringId: normalizedOffering(template.plan?.offeringId), searchGoal: template.plan?.searchGoal ?? OFFERING_GOALS[normalizedOffering(template.plan?.offeringId)] }); invalidateSearchPlan(); } }
function invalidateSearchPlan() { activeSearchPlan = null; $('planPanel').style.display = 'none'; window.WorkbenchWorkspace?.planInvalidated(); }
function renderPlanLimits() {
  try { const p = payload(); $('planLimitSummary').textContent = `平台：${p.platforms.map(platformLabel).join('、')}；最多 ${p.maxKeywords} 个词，每词 ${p.maxPagesPerKeyword} 页；全任务 ${p.maxNotes} 篇笔记、${p.maxCommentsTotal} 条评论，每篇最多 ${p.maxCommentsPerNote} 条；间隔 ${p.delayMin}–${p.delayMax} 秒。`; }
  catch (error) { $('planLimitSummary').textContent = systemText(error.message); }
}
async function search() {
  const button = $('searchBtn'); if (button.disabled) return;
  let body; try { body = payload(); } catch (error) { return notifyWorkbench(error.message); }
  button.disabled = true; button.textContent = '正在分析关键词…'; invalidateSearchPlan();
  try {
    const result = await postJson('/api/search/plan', body);
    if (!result?.planId || !result.plan) throw new Error('计划返回不完整，尚未启动搜索。');
    const offeringId = normalizedOffering(result.plan.offeringId ?? body.offeringId);
    if (offeringId !== body.offeringId) throw new Error('返回的计划服务与所选服务不一致，请重新分析。');
    activeSearchPlan = { ...result, plan: { ...result.plan, offeringId }, originalSeeds: JSON.stringify(body.seedKeywords), originalOffering: body.offeringId };
    $('planOffering').textContent = `本计划服务：${offeringLabel(offeringId)}${offeringId === FOCUSED_OFFERING ? '。合格需求按买方委托与原文证据筛选，待确认内容另列。' : '。按目标研究相关内容。'}`;
    $('planKeywords').value = splitKeywords(result.plan.keywords || body.seedKeywords).join('\n');
    $('planGoal').value = result.plan.searchGoal || body.searchGoal;
    $('planExclusions').value = splitKeywords(result.plan.excludeKeywords || body.excludeKeywords).join('，');
    const reasons = result.plan.keywordReasons || {};
    $('planReasons').textContent = Object.entries(reasons).map(([word, reason]) => `${word}：${displayText(reason)}`).join('\n');
    $('planWarnings').textContent = [].concat(result.plan.error || [], result.plan.warnings || [], result.plan.ambiguities || []).map(systemText).join('；') || '请检查歧义、排除条件与扩词范围。平台尚未被搜索。';
    const origin = result.plan.planningMode || result.plan.origin;
    $('planOrigin').textContent = `计划 ${result.planId} · 来源：${origin === 'manual' ? '手动计划（智能分析不可用）' : ['llm','model','ai',undefined,null,''].includes(origin) ? '智能分析' : systemCode(origin, '已保存的搜索规划')}`;
    $('planPanel').style.display = 'block'; renderPlanLimits(); $('planPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.WorkbenchWorkspace?.planReady();
    setStatus('关键词分析完成。编辑实际执行关键词，确认后才会搜索。', '✅');
  } catch (error) { setStatus(`计划生成失败：${error.message}。尚未启动搜索。`, '❌'); }
  finally { button.disabled = false; button.textContent = '生成搜索计划 →'; }
}
async function startConfirmedSearch() {
  const button = $('confirmSearchBtn'); if (button.disabled || !activeSearchPlan) return;
  let data, keywords, body;
  try {
    data = payload(); keywords = splitKeywords($('planKeywords').value);
    if (JSON.stringify(data.seedKeywords) !== activeSearchPlan.originalSeeds) throw new Error('种子关键词已更改，请重新分析。');
    if (data.offeringId !== activeSearchPlan.originalOffering) throw new Error('服务或研究模式已更改，请重新分析。');
    if (!keywords.length) throw new Error('请保留至少一个执行关键词。');
    if (keywords.length > data.maxKeywords) throw new Error('执行关键词数量超过搜索词上限，请调整计划或上限。');
    const confirmedPlan = { ...activeSearchPlan.plan, ...data, offeringId: activeSearchPlan.plan.offeringId, keywords, searchGoal: $('planGoal').value.trim() || data.searchGoal, excludeKeywords: splitKeywords($('planExclusions').value) };
    body = { planId: activeSearchPlan.planId, confirmedPlan, mode: $('batchMode').checked ? 'batch' : 'single' };
    if ($('batchMode').checked) {
      body.rounds = readNumber('batchRounds', '扫描轮数'); body.intervalMin = readNumber('intervalMin', '最短轮间隔'); body.intervalMax = readNumber('intervalMax', '最长轮间隔');
      if (body.intervalMax < body.intervalMin) throw new Error('最长轮间隔不能小于最短轮间隔。');
    }
  } catch (error) { return notifyWorkbench(error.message); }
  button.disabled = true; $('searchBtn').disabled = true;
  try {
    const task = await postJson('/api/tasks/start', body);
    if (task?.ok === false || !task?.taskId) throw new Error(task?.error || '启动任务未返回任务编号');
    window.WorkbenchWorkspace?.taskSwitching(String(task.taskId));
    cachedData = null;
    currentTaskId = String(task.taskId); localStorage.setItem('keywordWorkbenchTaskId', currentTaskId);
    renderTaskPanel(task); $('planPanel').style.display = 'none'; activeSearchPlan = null;
    renderPlaceholderResults('任务已启动', '确认计划已经固定，进度和阶段日志将持续更新。');
    $('searchBtn').textContent = '任务运行中'; $('stopBtn').style.display = 'block'; startPolling(currentTaskId); loadTaskHistory();
    window.WorkbenchWorkspace?.taskStarted(currentTaskId);
  } catch (error) { setStatus(`启动失败：${error.message}`, '❌'); $('searchBtn').disabled = false; }
  finally { button.disabled = false; }
}
async function saveReview(index) {
  const lead = cachedData?.leads?.leads?.[index]; if (!lead) return;
  const reviewTaskId = String(currentTaskId);
  const status = $(`reviewStatus-${index}`).value, note = $(`reviewNote-${index}`).value.trim();
  try {
    const result = await postJson('/api/leads/review', { leadId: lead.lead_id, status, note, taskId: currentTaskId, reviewBasis: lead.reviewBasis });
    lead.review = result.review || { status, note };
    if (String(currentTaskId) !== reviewTaskId || cachedData?.leads?.leads?.[index] !== lead) { notifyWorkbench(`任务 #${reviewTaskId} 的人工审核已保存。`, 'success'); return false; }
    $(`reviewResult-${index}`).textContent = `${reviewLabel(lead.review.status)} · 已保存`;
    if ($('reviewedCount')) $('reviewedCount').textContent = fmt((cachedData.leads?.leads || []).filter(item => item.review?.status === 'approved').length);
    window.WorkbenchWorkspace?.reviewSaved(index);
    return true;
  } catch (error) { if (String(currentTaskId) === reviewTaskId && cachedData?.leads?.leads?.[index] === lead) setSystemMessage(`reviewResult-${index}`, `保存失败：${error.message}`); else notifyWorkbench(`任务 #${reviewTaskId} 审核保存失败：${error.message}`); return false; }
}
function changeExportScope(value) { if (['approved', 'pending', 'all'].includes(value)) exportScope = value; window.WorkbenchWorkspace?.exportUpdated(); }
function selectedExportLeads() {
  const leads = cachedData?.leads?.leads || [];
  return leads.filter(lead => exportScope === 'all' || (lead.review?.status || 'pending') === exportScope);
}
function exportScopeLabel() { return ({ approved: '仅人工审核通过', pending: '待审核候选', all: '全部候选与相关内容（含未审核项）' })[exportScope]; }
function exportQualificationLabel(lead) {
  if (resultOfferingId(cachedData) !== FOCUSED_OFFERING) return '不适用（通用关键词研究）';
  return lead.qualified === true || lead.preQaQualified === true ? qualificationDisplayLabel(lead) : '未满足或未记录';
}
function exportExcel() {
  if (!cachedData) return notifyWorkbench('暂无可导出数据');
  const leads = selectedExportLeads();
  const rows = leads.map(l => ({ 平台: platformLabel(leadPlatform(l)), 用户昵称: l.nickname || '', 用户编号: l.user_id || leadTargetUserId(l) || '', 电话: l.phone || l.mobile || '', 邮箱: l.email || '', 人工审核: reviewLabel(l.review?.status), 审核备注: l.review?.note || '', 需求类别: demandLabel(l.demand_category || l.category || l.classification), 定制需求资格: exportQualificationLabel(l), 意图角色: intentRoleLabel(l.intent_role), 服务匹配: serviceFitLabel(l.service_fit), 需求来源: demandOriginLabel(l.demand_origin), 意图判断依据: l.intent_reason || '', 原文证据: l.evidence || '', 原始内容: l.content || l.comment || '', 需求摘要: l.demand_summary || '', 判定理由: l.reason || '', 质检状态: qaStatusLabel(l.qa_status), 评分: l.score, 优先级: priorityLabel(l.priority), 跟进草稿: l.outreach_copy || '', 来源笔记: l.note_title || '', 来源链接: exportSourceUrl(l.note_url), 采集时间: l.collected_at || l.collectedAt || '', 反馈: FEEDBACK_LABELS[l.feedback] || l.feedback || '' }));
  const selectedUrls = new Set(leads.map(l => l.note_url));
  const notes = (cachedData.notes || []).filter(n => exportScope === 'all' || selectedUrls.has(n.url));
  const sheets = [ { name: '需求候选', rows }, { name: '来源笔记', rows: notes.map(n => ({ 标题: n.title, 作者: n.author || '', 正文: n.content || n.desc || '', 日期: n.publish_date, 点赞: n.liked, 评论: n.comments, 链接: exportSourceUrl(n.url) })) }, { name: '导出说明', rows: [{ 导出范围: exportScopeLabel(), 任务编号: currentTaskId, 服务与模式: offeringLabel(resultOfferingId(cachedData)), 搜索目标: getDemandDescription(), 生成时间: new Date().toISOString(), 候选数: leads.length }] } ];
  for (const sheet of sheets) sheet.rows = sheet.rows.map(row => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, typeof value === 'string' ? redactExportText(value) : value])));
  OfflineExcel.download(sheets, `关键词研判_${exportScopeLabel()}_${new Date().toISOString().slice(0, 10)}.xlsx`);
}
function downloadText(text, name, type) {
  const blob = new Blob([text], { type }), url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportMarkdown() {
  if (!cachedData) return notifyWorkbench('暂无可导出数据');
  const leads = selectedExportLeads(), report = cachedData.report || {};
  const literal = value => redactExportText(value).replace(/[\\`*_{}\[\]<>#!|]/g, '\\$&');
  const lines = ['# 关键词检索与需求研判报告', '', `- 导出范围：${exportScopeLabel()}`, `- 任务：${currentTaskId}`, `- 服务与模式：${offeringLabel(resultOfferingId(cachedData))}`, `- 搜索目标：${literal(getDemandDescription())}`, `- 候选数：${leads.length}`, '', '## 需求候选'];
  leads.forEach((lead, index) => lines.push('', `### ${index + 1}. ${literal(lead.demand_summary || '需求候选')}`, `- 用户昵称：${literal(lead.nickname)}`, `- 用户编号：${literal(lead.user_id || leadTargetUserId(lead))}`, `- 电话：${literal(lead.phone || lead.mobile)}`, `- 邮箱：${literal(lead.email)}`, `- 人工审核：${reviewLabel(lead.review?.status)}`, `- 审核备注：${literal(lead.review?.note)}`, `- 需求类别：${literal(demandLabel(lead.demand_category || lead.category || lead.classification))}`, `- 意图角色：${intentRoleLabel(lead.intent_role)}；服务匹配：${serviceFitLabel(lead.service_fit)}；需求来源：${demandOriginLabel(lead.demand_origin)}`, `- 定制需求资格：${exportQualificationLabel(lead)}`, `- 质检状态：${qaStatusLabel(lead.qa_status)}`, `- 意图判断依据：${literal(lead.intent_reason)}`, `- 原文证据：${literal(lead.evidence)}`, `- 原始内容：${literal(lead.content || lead.comment)}`, `- 判定理由：${literal(lead.reason)}`, `- 来源：${literal(exportSourceUrl(lead.note_url))}`, `- 草稿：${literal(lead.outreach_copy)}`));
  if (exportScope === 'all') lines.push('', '## 全部相关内容摘要（含未审核内容）', literal(reportText(report.summary || '')), '', ...(cachedData.notes || []).map(n => `- ${literal(n.title)} · 作者：${literal(n.author)}：${literal(exportSourceUrl(n.url))}\n  ${literal(n.content || n.desc || '')}`));
  lines.push('', '## 采集限制与待确认事项', ...[].concat(report.risks || ['结果依赖本次确认的关键词、平台可见内容及采集范围。']).map(v => `- ${literal(reportText(v))}`));
  downloadText(lines.join('\n'), `关键词研判_${exportScopeLabel()}_${new Date().toISOString().slice(0, 10)}.md`, 'text/markdown;charset=utf-8');
}

function confirmFollowLead(index) { openOutreach(index, 'follow'); }
function openDmDraft(index) { openOutreach(index, 'send_dm'); }
function openOutreach(index, action) {
  const lead = cachedData?.leads?.leads?.[index]; if (!lead || !ACTION_LABELS[action]) return;
  const unresolved = unresolvedOutreach()[outreachKey(lead, action)];
  if (unresolved) { recoverLastOutreach(unresolved); return; }
  ++outreachDraftGeneration;
  outreachContext = { index, action, lead, taskId: currentTaskId }; preparedOutreach = null; outreachBusy = false; outreachSubmitted = false;
  currentDraftLeadIndex = index; $('outreachTitle').textContent = ACTION_LABELS[action];
  $('dmDraftMeta').textContent = `${platformLabel(leadPlatform(lead))} · ${lead.nickname || '目标用户'} · ${lead.user_id || leadTargetUserId(lead) || '待平台核对'}`;
  $('outreachSource').textContent = `${action === 'reply_comment' ? '回复原评论' : '来源证据'}：${redactExportText(lead.content || lead.evidence || '未记录')}\n${redactExportText(lead.note_title || '')}\n${exportSourceUrl(lead.note_url)}`;
  $('dmDraftText').value = action === 'follow' ? '' : redactExportText(outreachDraft(lead)); $('dmDraftText').disabled = action === 'follow';
  $('draftInstruction').disabled = action === 'follow'; $('aiDraftBtn').disabled = action === 'follow';
  $('prepareOutreachBtn').style.display = 'inline-flex'; $('prepareOutreachBtn').disabled = false;
  updateOutreachPrepareButton(false);
  $('executeOutreachBtn').style.display = 'none'; $('executeOutreachBtn').disabled = false;
  $('refreshOutreachBtn').style.display = 'none'; $('outreachConfirmation').style.display = 'none';
  $('dmDraftStatus').style.color = ''; $('dmDraftStatus').textContent = '先核对当前账号、对象及最终正文，然后确认本条动作。'; $('dmDraftModal').style.display = 'flex';
  window.WorkspaceUI?.showDialog($('dmDraftModal'));
}
function closeDmDraft() {
  if (outreachBusy) { $('dmDraftStatus').textContent = '动作正在处理中，请等待状态返回。'; return; }
  ++outreachDraftGeneration;
  $('dmDraftModal').style.display = 'none';
  window.WorkspaceUI?.closeDialog($('dmDraftModal'));
  // Keep the action snapshot for explicit status checks; closing never cancels/repeats a write.
}
function invalidateOutreachConfirmation() {
  if (outreachSubmitted || outreachBusy) return;
  preparedOutreach = null; $('executeOutreachBtn').style.display = 'none'; $('outreachConfirmation').style.display = 'none';
  $('prepareOutreachBtn').disabled = false; $('dmDraftStatus').style.color = ''; $('dmDraftStatus').textContent = '正文已更改，请重新核对账号与动作。';
  updateOutreachPrepareButton(false);
}
function updateOutreachPrepareButton(prepared) { const button = $('prepareOutreachBtn'); button.classList.toggle('wb-prepared', prepared); button.textContent = prepared ? '重新核对' : '核对账号与动作'; }
function openLeadProfileFromDraft() {
  const url = leadProfileUrl(outreachContext?.lead); if (!url || safeUrl(url) === '#') return notifyWorkbench('没有可打开的对象主页。');
  window.open(safeUrl(url), '_blank', 'noopener,noreferrer');
}
async function copyDmDraft() {
  const text = redactExportText($('dmDraftText').value.trim()); if (!text) return notifyWorkbench('正文为空。');
  try { if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text); else { const input = $('dmDraftText'), original = input.value; try { input.value = text; input.select(); if (!document.execCommand('copy')) throw new Error('请手动复制选中的正文'); } finally { input.value = original; } } $('dmDraftStatus').textContent = '正文已复制，未发送。'; }
  catch (error) { setSystemMessage('dmDraftStatus', `复制失败：${error.message}`); }
}
async function generateOutreachDraft() {
  if (!outreachContext || outreachSubmitted || outreachBusy) return;
  const context = outreachContext, generation = ++outreachDraftGeneration;
  const originalText = $('dmDraftText').value, instruction = $('draftInstruction').value.trim();
  const stillCurrent = () => generation === outreachDraftGeneration && context === outreachContext;
  $('aiDraftBtn').disabled = true;
  try {
    const draft = await postJson('/api/outreach/draft', { leadId: context.lead.lead_id, taskId: context.taskId, action: context.action, instruction });
    if (!stillCurrent() || outreachSubmitted || outreachBusy) return;
    if ($('dmDraftText').value !== originalText || $('draftInstruction').value.trim() !== instruction) {
      $('dmDraftStatus').textContent = '生成期间正文或要求已修改，已保留你的输入。需要新草稿时请重新生成。';
      return;
    }
    $('dmDraftText').value = redactExportText(draft.text || '');
    invalidateOutreachConfirmation();
  } catch (error) { if (stillCurrent()) setSystemMessage('dmDraftStatus', `草稿生成失败：${error.message}`); }
  finally { if (stillCurrent()) $('aiDraftBtn').disabled = context.action === 'follow' || outreachBusy || outreachSubmitted; }
}
function renderOutreachConfirmation(result, action, text) {
  const valueText = value => ['string', 'number'].includes(typeof value) ? String(value) : '';
  const account = result.currentAccount || result.account || result.accountIdentity;
  const accountName = account && typeof account === 'object' ? valueText(account.name || account.nickname) : '';
  const accountId = account && typeof account === 'object' ? valueText(account.identity || account.userId || account.user_id) : valueText(account);
  const target = result.target && typeof result.target === 'object' ? result.target : {};
  const targetUrl = valueText(target.url || target.targetUrl || result.targetUrl), link = safeUrl(targetUrl);
  const accountText = [accountName, accountId || valueText(result.accountIdentity)].filter(Boolean).join(' · ') || '未确认';
  const rows = [
    ['当前账号', accountText],
    ['平台 / 动作', `${platformLabel(result.platform || leadPlatform(outreachContext?.lead))} · ${ACTION_LABELS[result.action || action] || '未确认'}`],
    ['目标用户编号', valueText(target.userId || target.targetUserId || result.targetUserId)],
    ['来源笔记编号', valueText(target.noteId || result.noteId)],
    ['来源评论编号', valueText(target.commentId || result.commentId)],
  ].filter(([, value]) => value);
  const finalText = result.text === undefined ? (action === 'follow' ? '关注动作无需正文。' : text) : String(result.text || (action === 'follow' ? '关注动作无需正文。' : ''));
  $('outreachConfirmation').innerHTML = `<h3 id="outreachConfirmationTitle">核对本条动作</h3><dl class="wb-outreach-confirm-fields">${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}${targetUrl ? `<div><dt>目标链接</dt><dd>${link === '#' ? esc(targetUrl) : `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(targetUrl)}</a>`}</dd></div>` : ''}<div><dt>动作编号</dt><dd>${esc(result.actionId)}</dd></div></dl><div class="wb-outreach-confirm-copy"><h4>完整正文</h4><blockquote>${esc(finalText)}</blockquote></div><p class="wb-outreach-confirm-boundary">确认仅授权以上这一条动作；修改正文后需要重新核对。</p>`;
}
async function prepareOutreach() {
  if (!outreachContext || outreachBusy || outreachSubmitted) return;
  const { lead, action, taskId } = outreachContext, text = action === 'follow' ? '' : $('dmDraftText').value.trim();
  if (action !== 'follow' && !text) return notifyWorkbench('请填写正文。');
  outreachBusy = true; preparedOutreach = null; $('aiDraftBtn').disabled = true; $('prepareOutreachBtn').disabled = true; $('executeOutreachBtn').style.display = 'none';
  $('dmDraftStatus').style.color = ''; $('dmDraftStatus').textContent = '正在核对账号和目标；此步骤尚未发送…';
  try {
    const result = await postJson('/api/outreach/prepare', { platform: leadPlatform(lead), action, leadId: lead.lead_id, taskId, targetUserId: action === 'comment' ? '' : leadTargetUserId(lead), noteId: ['comment', 'reply_comment'].includes(action) ? lead.note_id : '', commentId: action === 'reply_comment' ? lead.comment_id : '', targetUrl: ['follow', 'send_dm'].includes(action) ? leadProfileUrl(lead) : lead.note_url, text });
    if (!result?.actionId || !result?.confirmationToken) throw new Error('未返回有效确认凭证，动作未执行。');
    if ($('dmDraftText').value.trim() !== text && action !== 'follow') throw new Error('核对期间正文被修改，请重新核对。');
    preparedOutreach = { ...result, finalText: text };
    renderOutreachConfirmation(result, action, text);
    $('outreachConfirmation').style.display = 'block'; $('executeOutreachBtn').style.display = 'inline-flex';
    $('dmDraftStatus').textContent = '请检查上方账号、目标与完整正文。未点击确认前不会执行。';
    $('outreachConfirmation').scrollIntoView?.({ block: 'nearest' });
  } catch (error) { setSystemMessage('dmDraftStatus', `核对失败：${error.message}`); }
  finally { outreachBusy = false; $('prepareOutreachBtn').disabled = false; $('aiDraftBtn').disabled = action === 'follow'; updateOutreachPrepareButton(Boolean(preparedOutreach)); }
}
async function executeOutreach() {
  if (!preparedOutreach || outreachBusy || outreachSubmitted) return;
  if (outreachContext.action !== 'follow' && $('dmDraftText').value.trim() !== preparedOutreach.finalText) return invalidateOutreachConfirmation();
  outreachBusy = true; outreachSubmitted = true;
  $('executeOutreachBtn').disabled = true; $('prepareOutreachBtn').disabled = true; $('dmDraftText').disabled = true; $('aiDraftBtn').disabled = true;
  $('dmDraftStatus').textContent = '正在执行；网络中断时不要重复发送，请查询动作状态。';
  sessionStorage.setItem('keywordWorkbenchLastAction', preparedOutreach.actionId); $('recoverOutreachBtn').style.display = 'inline-flex';
  try {
    const result = await postJson('/api/outreach/execute', { actionId: preparedOutreach.actionId, confirmationToken: preparedOutreach.confirmationToken });
    renderOutreachResult(result);
  } catch (error) { renderOutreachResult({ status: 'unknown', error: `请求未取得可靠回执：${error.message}` }); }
  finally { outreachBusy = false; $('refreshOutreachBtn').style.display = 'inline-flex'; loadOutreachHistory(); }
}
function renderOutreachResult(result) {
  if (result?.text !== undefined && !outreachContext) $('dmDraftText').value = result.text;
  const status = result?.status || 'unknown';
  const verified = ['succeeded', 'success', 'verified_success', 'already_following'].includes(status);
  const unknown = ['unknown', 'pending_verification', 'executing', 'submitted'].includes(status);
  const label = verified ? '平台结果已核验' : unknown ? '结果待核实，禁止自动重发' : '动作未成功；请查看原因及原动作状态';
  const actionId = result?.actionId || preparedOutreach?.actionId || '';
  const unresolved = unresolvedOutreach();
  if (unknown && outreachContext) unresolved[outreachKey(outreachContext.lead, outreachContext.action)] = actionId;
  if (!unknown) for (const key of Object.keys(unresolved)) { if (unresolved[key] === actionId) delete unresolved[key]; }
  sessionStorage.setItem('keywordWorkbenchUnresolvedActions', JSON.stringify(unresolved));
  const message = result?.message || result?.error || '', prefix = `${label} · ${actionId}\n`;
  setSystemMessage('dmDraftStatus', message, prefix); $('dmDraftStatus').style.color = verified ? 'var(--green)' : 'var(--red)';
  const inline = outreachContext && $(`outreachStatus-${outreachContext.index}`); if (inline) setSystemMessage(inline, message, prefix);
  $('executeOutreachBtn').style.display = 'none'; $('prepareOutreachBtn').style.display = 'none';
  // Terminal and uncertain states remain non-replayable in this dialog.
}
async function refreshOutreachStatus() {
  const actionId = preparedOutreach?.actionId || sessionStorage.getItem('keywordWorkbenchLastAction'); if (!actionId || outreachBusy) return;
  outreachBusy = true;
  try { renderOutreachResult(await apiGet(`/api/outreach/status?actionId=${encodeURIComponent(actionId)}`)); }
  catch (error) { setSystemMessage('dmDraftStatus', `尚无法核实动作 ${actionId}：${error.message}。不要重复发送。`); }
  finally { outreachBusy = false; }
}

$('searchBtn').addEventListener('click', search);
$('keyword').addEventListener('input', invalidateSearchPlan);
for (const id of ['demandDescription', 'excludeKeywords', 'industry', 'region']) $(id).addEventListener('input', invalidateSearchPlan);
for (const id of [...Object.values(LIMIT_FIELDS), 'delayMin', 'delayMax', 'timeFilter', 'sort', 'platformXhs', 'platformDouyin']) $(id).addEventListener('change', renderPlanLimits);
window.addEventListener('DOMContentLoaded', bootSales);

function collectionEndLabel(reasons) {
  if (window.DisplayText) return window.DisplayText.stopReasons(reasons) || '采集中';
  return (Array.isArray(reasons) ? reasons : reasons ? [reasons] : []).map(reason => ({ note_quota_reached: '已到笔记上限', comment_quota_reached: '已到评论上限', configured_pages_completed: '设定页数已完成' })[reason] || (/no_more_unique_results/.test(reason) ? '平台没有更多不重复结果' : systemCode(reason, '采集已结束，具体原因未识别'))).join('；') || '采集中';
}
async function recoverLastOutreach(selectedId) {
  const actionId = selectedId || sessionStorage.getItem('keywordWorkbenchLastAction'); if (!actionId) return;
  ++outreachDraftGeneration;
  outreachContext = null; preparedOutreach = { actionId }; outreachSubmitted = true; outreachBusy = false;
  $('outreachTitle').textContent = '核实先前触达动作'; $('dmDraftMeta').textContent = `动作 ${actionId}`;
  $('outreachSource').textContent = '仅查询已有动作，不会重发。'; $('dmDraftText').value = ''; $('dmDraftText').disabled = true;
  $('draftInstruction').disabled = true; $('aiDraftBtn').disabled = true; $('prepareOutreachBtn').style.display = 'none'; $('executeOutreachBtn').style.display = 'none';
  $('outreachConfirmation').style.display = 'none'; $('refreshOutreachBtn').style.display = 'inline-flex'; $('dmDraftModal').style.display = 'flex';
  window.WorkspaceUI?.showDialog($('dmDraftModal'));
  await refreshOutreachStatus();
}

async function loadOutreachHistory() {
  if (!authenticated) return;
  try {
    const result = await apiGet('/api/outreach/list');
    const actions = result.actions || [];
    const unresolved = unresolvedOutreach();
    for (const action of actions) {
      const key = JSON.stringify([action.platform, action.action, ['follow', 'send_dm'].includes(action.action) ? action.targetUserId : action.noteId, action.action === 'reply_comment' ? action.commentId : '']);
      if (['unknown', 'executing'].includes(action.status)) unresolved[key] = action.actionId || action.id;
      else if (unresolved[key] === (action.actionId || action.id) && ['succeeded', 'failed'].includes(action.status)) delete unresolved[key];
    }
    sessionStorage.setItem('keywordWorkbenchUnresolvedActions', JSON.stringify(unresolved));
    $('outreachHistoryList').innerHTML = actions.length ? actions.map(action => `<article class="history-item wb-outreach-record"><div class="wb-outreach-record-body"><div class="wb-outreach-record-heading"><h3>${esc(ACTION_LABELS[action.action] || systemCode(action.action, '触达动作'))} · ${esc(platformLabel(action.platform))}</h3><span class="wb-status wb-status-${esc(action.status)}">${esc(({prepared:'待确认',executing:'执行中',succeeded:'已核验成功',failed:'失败',unknown:'结果待核实'})[action.status] || systemCode(action.status, '状态未记录'))}</span></div><p class="wb-outreach-target"><span>目标</span>${esc(action.targetUserId || action.noteId || action.targetUrl || '未记录')}</p>${action.error ? `<div class="wb-outreach-error">${esc(systemText(action.error))}${systemText(action.error) !== action.error ? technicalDetails(action.error) : ''}</div>` : ''}<div class="wb-outreach-record-footer"><span class="small">动作 ${esc(action.actionId || action.id)}</span><button type="button" class="btn-secondary" data-action-id="${esc(action.actionId || action.id)}" onclick="recoverLastOutreach(this.dataset.actionId)">核实状态</button></div></div></article>`).join('') : '<div class="wb-empty"><h3>暂无触达记录</h3><p>完成单条动作确认后，可在这里核实执行结果。</p></div>';
    window.WorkbenchWorkspace?.connectionUpdated(true);
  } catch (error) { window.WorkbenchWorkspace?.connectionUpdated(false, `动作记录同步失败：${error.message}`); if (!$('outreachHistoryList').querySelector?.('.history-item')) setSystemMessage('outreachHistoryList', `动作记录读取失败：${error.message}`); }
}

function captureResultFormState() {
  if (renderedTaskId !== currentTaskId) resultFormDrafts = {};
  const forms = {};
  (cachedData?.leads?.leads || []).forEach((lead, index) => {
    const values = {};
    for (const prefix of ['reviewStatus', 'reviewNote', 'feedbackType', 'feedbackNote']) { const el = $(`${prefix}-${index}`); if (el) values[prefix] = el.value; }
    if (Object.keys(values).length) forms[lead.lead_id] = values;
  });
  if (renderedTaskId === currentTaskId) Object.assign(resultFormDrafts, forms);
  return { forms: { ...resultFormDrafts }, activeTab: document.querySelector?.('[data-result-tab].active')?.dataset?.resultTab || 'leads', taskId: renderedTaskId };
}
function restoreResultFormState(state) {
  const sameTask = state?.taskId === currentTaskId; renderedTaskId = currentTaskId;
  if (!sameTask) return;
  (cachedData?.leads?.leads || []).forEach((lead, index) => {
    for (const [prefix, value] of Object.entries(state.forms[lead.lead_id] || {})) { const el = $(`${prefix}-${index}`); if (el) el.value = value; }
  });
  switchResultTab(state.activeTab);
}
async function viewReviewHistory(index) {
  const lead = cachedData?.leads?.leads?.[index]; if (!lead) return;
  const reviewTaskId = String(currentTaskId), reviewLeadId = String(lead.lead_id);
  try {
    const result = await apiGet(`/api/leads/review-history?leadId=${encodeURIComponent(reviewLeadId)}&taskId=${encodeURIComponent(reviewTaskId)}`);
    if (String(currentTaskId) !== reviewTaskId || String(cachedData?.leads?.leads?.[index]?.lead_id) !== reviewLeadId) return;
    if (window.WorkbenchWorkspace) window.WorkbenchWorkspace.reviewHistory(result.history || []);
    else alert((result.history || []).map(item => `${reviewLabel(item.status)} · ${formatTime(item.created)} · ${item.note || '无备注'}`).join('\n') || '尚无人工审核记录。');
  } catch (error) { if (String(currentTaskId) === reviewTaskId) notifyWorkbench(`审核记录读取失败：${error.message}`); }
}

function outreachKey(lead, action) {
  return JSON.stringify([leadPlatform(lead), action, ['follow', 'send_dm'].includes(action) ? leadTargetUserId(lead) : lead.note_id, action === 'reply_comment' ? lead.comment_id : '']);
}
function unresolvedOutreach() {
  try { const value = JSON.parse(sessionStorage.getItem('keywordWorkbenchUnresolvedActions') || '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; }
}

function exportSourceUrl(value) {
  if (!String(value || '').trim()) return '';
  const safe = safeUrl(value); if (safe === '#') return '';
  return safe;
}
function redactExportText(value) {
  // Business contacts, account identifiers and literal source evidence remain
  // available for follow-up. Only system credentials are removed from sharing.
  return String(value ?? '').split(/(https?:\/\/[^\s<>"'，；。！？、,;]+)/g).map(segment => {
    if (/^https?:\/\//.test(segment)) return exportSourceUrl(segment);
    return segment
      .replace(/(^|\n)(\s*(?:cookie|set-cookie)\s*:\s*)[^\r\n]*/gi, '$1$2[凭据已脱敏]')
      .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi, 'Bearer [凭据已脱敏]')
      .replace(/\b(?:sk-|sdx-)[A-Za-z0-9_.-]{12,}/gi, '[凭据已脱敏]')
      .replace(/(\b["']?(?:cookies?|set-cookie|authorization|proxy-authorization|api[_-]?key|x-api-key|password|passwd|secret|client[_-]?secret|token|access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?(?:id|token)|xsec[_-]?token)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;，；]+)/gi, '$1[凭据已脱敏]');
  }).join('');
}
