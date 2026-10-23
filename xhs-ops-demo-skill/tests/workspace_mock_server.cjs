/* Local UI fixture only. No imports of business code, databases, accounts or model adapters.
 * node tests/workspace_mock_server.cjs       -> one local URL; Ctrl-C to stop
 * node tests/workspace_mock_server.cjs --check -> fixture consistency check, no listening socket
 * POST /__fixture/config modifies only this process's memory. Never run as a shared service.
 */
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const assets = path.resolve(__dirname, '../assets'), clone = value => JSON.parse(JSON.stringify(value));
const now = 1792555200;
function makeState() {
  const plan = { offeringId: 'agent_custom_development', seedKeywords: ['智能体定制', '客服外包'], keywords: ['智能体定制', '客服外包'], searchGoal: '寻找有具体场景并明确求开发与报价的智能体委托', platforms: ['xhs', 'douyin'], excludeKeywords: ['教程'], maxKeywords: 6, maxPagesPerKeyword: 1, maxNotes: 30, maxCommentsPerNote: 20, maxCommentsTotal: 100, readDetail: true, scanLeads: true, rounds: 1, intervalMinutes: 5 };
  const common = { platform: 'xhs', note_id: 'fixture-note-1', note_title: '客服智能体的实施与交付', note_url: 'https://www.xiaohongshu.com/explore/fixture-note-1', comment_id: 'fixture-comment-1', target_user_id: 'fixture-buyer-1', nickname: '示例客户', reviewBasis: 'fixture-basis', review: { status: 'pending', note: '' }, qa_status: 'approved', priority: 'B', score: 87, intent_role: 'buyer', demand_origin: 'self', service_fit: 'custom_development', risk: '预算与交付周期待核实' };
  const leads = [
    { ...common, lead_id: 'lead-qualified-a', classification: 'demand', qualified: true, demand_summary: '电商客服智能体定制与报价', evidence: '我们有一个20人的客服团队，找人开发客服智能体，请带方案报价。', content: '我们有一个20人的客服团队，找人开发客服智能体，请带方案报价。', reason: '原文同时说明本人场景和寻求外部开发。', intent_reason: '买方明确寻找开发与报价', custom_need_evidence: ['客服团队'], outsourcing_evidence: ['找人开发客服智能体'] },
    { ...common, lead_id: 'lead-uncertain-b', comment_id: 'fixture-comment-2', target_user_id: 'fixture-buyer-2', nickname: '示例待确认', classification: 'uncertain', qualified: false, score: 59, qa_status: 'pending', demand_summary: '内部知识库助手：实施范围待确认', evidence: '团队想做知识库助手，有人能聊聊吗？', reason: '有场景，但未明确外包意图，不能标为合格委托。' },
    { ...common, lead_id: 'lead-promotion-c', comment_id: 'fixture-comment-3', classification: 'promotion', qualified: false, score: 20, intent_role: 'supplier', demand_summary: '供方宣传，不属于买方需求', evidence: '提供智能体定制开发服务，欢迎咨询。', reason: '供方推广，保留分类而不混入待审核需求。' },
    { ...common, platform: 'douyin', lead_id: 'lead-approved-d', note_id: 'fixture-note-2', comment_id: 'fixture-comment-4', target_user_id: 'fixture-buyer-4', nickname: '示例已复核', note_title: '制造企业流程自动化', note_url: 'https://www.douyin.com/video/fixture-note-2', classification: 'demand', qualified: true, demand_summary: '制造业报价助手交付', evidence: '我负责工厂报价系统，想外包一个智能体，有经验的报个价。', reason: '本人业务场景和外包报价意图明确。', review: { status: 'approved', note: '隔离测试：已对照原文核验' }, score: 91 },
  ];
  const coverage = Object.fromEntries(['search', 'relevance', 'details', 'comments', 'lead_signal', 'qa', 'report'].map(key => [key, { status: 'completed', completed: key === 'report' ? 1 : key === 'search' ? 4 : key === 'lead_signal' || key === 'qa' ? 4 : 2, total: key === 'report' ? 1 : key === 'search' ? 4 : key === 'lead_signal' || key === 'qa' ? 4 : 2, unit: key === 'search' ? 'query' : key === 'report' ? 'report' : key === 'lead_signal' || key === 'qa' ? 'candidate' : 'note' }]));
  const result = { taskId: 4, confirmedPlan: plan, searchGoal: plan.searchGoal, notes: [{ id: 'fixture-note-1', rank: 1, platform: 'xhs', title: '客服智能体的实施与交付', author: '示例作者', url: common.note_url, comments: 3, content: '隔离示例：讨论客服团队的智能体需求和交付路径。' }, { id: 'fixture-note-2', rank: 2, platform: 'douyin', title: '制造企业流程自动化', author: '示例工厂', url: leads[3].note_url, comments: 1, content: '隔离示例：制造企业报价流程自动化。' }], leads: { totalCommentsScanned: 4, leads }, report: { summary: '本次示例识别两条有明确外包意图的需求、一条待确认意向及一条供方宣传。请优先审核原文并核实预算。', opportunities: ['电商客服团队', '制造业报价流程'], risks: ['预算未公开'] }, qa: { summary: '来源及引用已经模型检查，人工判断仍独立记录。' }, stageCoverage: coverage, collectionSummary: { notesCollected: 2, commentsCollected: 4, requested: { maxNotes: 30, maxCommentsTotal: 100 }, endReasons: ['configured_pages_completed'] } };
  const trace = [{ id: 1, agentKey: 'relevance_agent', purpose: 'relevance', promptVersion: 'keyword-v3', runType: 'main', runId: 'fixture-run-1', status: 'completed', durationMs: 1240, createdAt: now, confidence: 0.91, inputSummary: { noteCount: 2 }, outputSummary: { summary: '两篇来源与搜索目标相关。' } }, { id: 2, agentKey: 'lead_signal_agent', purpose: 'lead_signal', promptVersion: 'keyword-v3', runType: 'main', runId: 'fixture-run-2', status: 'completed', durationMs: 3860, createdAt: now + 3, confidence: 0.87, inputSummary: { commentCount: 4 }, outputSummary: { summary: '合格需求 2 条，待确认 1 条，供方宣传 1 条。' } }];
  const task4 = { taskId: 4, id: 4, hasTask: true, status: 'completed', isRunning: false, statusText: '采集与分析已完成', confirmedPlan: plan, keyword: '智能体定制，客服外包', updatedAt: now + 30, createdAt: now, progress: { percent: 100 }, result, leads: result.leads, notes: result.notes, stageCoverage: coverage, agentTrace: trace, events: [{ createdAt: now, agent: 'system', message: '计划已由用户确认' }, { createdAt: now + 30, agent: 'report', message: '任务完成，等待人工审核' }], taskSummary: { task_summary: '已识别 2 条合格委托，仍需人工核对和补充预算。' }, notesCount: 2, leadsCount: 4 };
  const task2 = clone(task4); Object.assign(task2, { taskId: 2, id: 2, status: 'partial', statusText: '部分完成：评论读取失败，质检超时', updatedAt: now - 3600 }); task2.result.taskId = 2; task2.result.stageCoverage.comments = { status: 'partial', completed: 1, total: 2, unit: 'note', error: '示例评论读取失败' }; task2.result.stageCoverage.qa = { status: 'failed', completed: 0, total: 4, unit: 'candidate', error: '示例模型总时限超时' }; task2.result.stageCoverage.report = { status: 'skipped', completed: 0, total: 1, unit: 'report' }; task2.stageCoverage = task2.result.stageCoverage;
  const task5 = clone(task4); Object.assign(task5, { taskId: 5, id: 5, status: 'running', isRunning: true, statusText: '正在分析候选原文', updatedAt: now + 60, progress: { percent: 65 } }); task5.result.taskId = 5; task5.stageCoverage.lead_signal.status = 'running'; task5.stageCoverage.qa.status = 'pending'; task5.stageCoverage.report.status = 'pending';
  const samples = [{ id: 'sample-1', noteId: 'fixture-note-1', topic: '客服', sourceUrl: common.note_url, collectedAt: '2026-10-21', text: leads[0].evidence, split: 'acceptance', labels: { relevant: true, classification: 'demand' }, annotator: '隔离验收', sourceVerified: true }, { id: 'sample-2', noteId: 'fixture-note-2', topic: '知识库', sourceUrl: common.note_url, collectedAt: '2026-10-21', text: leads[1].evidence, split: 'acceptance', labels: { relevant: true, classification: 'uncertain' }, annotator: '隔离验收', sourceVerified: true }];
  const evaluation = { runId: 'eval-passed-8', datasetId: 'dataset-fixture', promptId: 8, promptHash: 'fixture-only-hash', split: 'acceptance', createdAt: now, autoPassed: true, passed: true, humanReviewComplete: true, humanReview: { note: '示例核验记录，仅用于界面测试' }, metrics: { relevance: { precision: 0.95, recall: 0.9 }, demand: { precision: 0.9, recall: 0.88 } }, requirements: { atLeast100Samples: false, acceptanceRun: true }, rows: samples.map(sample => ({ id: sample.id, labels: sample.labels, classification: sample.labels.classification, relevant: true, evidence: [sample.text], citationPassed: true, fabricationPassed: true, prediction: { demand_summary: sample.text, reason: '示例模型解释' } })) };
  return addUiAuditStates({ loggedIn: true, options: { offline: false, failPaths: [], task5CompleteAfter: 0 }, calls: [], counts: {}, lastPayload: {}, statusPolls: 0, nextTaskId: 6, tasks: [task4, task2, task5], plan, settings: { llm: { api_base: 'https://fixture.invalid/v1', model: 'fixture-model', has_api_key: false, temperature: 0.6, connect_timeout_s: 30, read_timeout_s: 60, total_timeout_s: 300 }, platforms: { enabled: ['xhs', 'douyin'] }, xhs: { bin: 'fixture-xhs', enable_follow_actions: false }, opencli: { bin: 'fixture-opencli' }, douyin: { enable_follow_actions: false, enable_dm_actions: false }, sales: { redact_user: true } }, templates: [{ name: '客服智能体委托', plan }], actions: [], reviews: [], samples, evaluations: [evaluation, { ...clone(evaluation), runId: 'eval-pending-9', promptId: 9, passed: false, humanReviewComplete: false, humanReview: null }], prompts: [{ id: 7, agentKey: 'lead_signal_agent', agentName: '需求识别', status: 'active', iterationVersion: 'V0.1', version: 'keyword-v3', prompt: '只根据原文识别需求，并说明判断依据。', notes: '当前使用版本', sourceTaskId: 4, createdAt: now }, { id: 8, agentKey: 'lead_signal_agent', agentName: '需求识别', status: 'candidate', iterationVersion: 'V0.2', version: 'keyword-v3-candidate', prompt: '只根据原文识别需求；同时核对本人场景与明确的外包报价意图。', notes: '完善买方委托证据要求', sourceTaskId: 4, createdAt: now + 10 }, { id: 9, agentKey: 'lead_signal_agent', agentName: '需求识别', status: 'candidate', iterationVersion: 'V0.3', version: 'keyword-v3-candidate-next', prompt: '增加预算与交付周期的核验提示。', notes: '待人工验收', sourceTaskId: 2, createdAt: now + 20 }], feedback: [{ id: 1, taskId: 4, leadId: 'lead-uncertain-b', feedbackType: 'invalid_lead', feedback: '还没有明确委托意图，请保守判断', userNote: '请保留待确认状态', evidence: '团队想做知识库助手，有人能聊聊吗？', status: 'pending', createdAt: now, updatedAt: now }, { id: 2, taskId: 4, leadId: 'lead-qualified-a', feedbackType: 'valid_lead', feedback: '买方角色识别正确', userNote: '保留具体原文依据', status: 'applied', candidatePromptId: 8, createdAt: now - 20, updatedAt: now - 20 }] });
}
function addUiAuditStates(state) {
  state.hasAdmin = true;
  Object.assign(state.options, { loginError: '', setupError: '', xhsLoginState: 'idle', douyinLoginState: 'idle', outreachOutcome: 'unknown' });
  const empty = clone(state.tasks[0]);
  Object.assign(empty, { id: 3, taskId: 3, keyword: '暂未发现需求的研究范围', status: 'completed', statusText: '采集已完成，本次没有符合范围的内容', notesCount: 0, leadsCount: 0, agentTrace: [], taskSummary: { task_summary: '结果为零，不使用无关内容补足候选。' } });
  empty.result = { taskId: 3, confirmedPlan: empty.confirmedPlan, notes: [], leads: { leads: [], totalCommentsScanned: 0 }, report: { summary: '本次未发现符合研究范围的内容。可调整关键词或稍后重试。' }, qa: {}, stageCoverage: Object.fromEntries(Object.keys(empty.stageCoverage).map(key => [key, { ...empty.stageCoverage[key], completed: 0, total: 0, status: key === 'search' ? 'completed' : 'skipped' }])) };
  Object.assign(empty, { leads: empty.result.leads, notes: [], stageCoverage: empty.result.stageCoverage });
  const failed = clone(empty);
  Object.assign(failed, { id: 1, taskId: 1, keyword: '需要重新验证登录的任务', status: 'failed', statusText: '读取失败：平台登录状态已过期，请人工验证后重试', errorText: '隔离模拟：平台登录已过期', progress: { percent: 0 }, events: [{ createdAt: now, agent: 'system', message: '隔离模拟：未执行采集；登录校验失败。' }], taskSummary: { task_summary: '任务失败，未产生可审核候选。' } });
  failed.result.taskId = 1; failed.result.report = {}; failed.result.stageCoverage.search = { status: 'failed', completed: 0, total: 4, unit: 'query', error: '隔离模拟：登录状态过期' };
  failed.stageCoverage = failed.result.stageCoverage;
  state.tasks.push(empty, failed);
  state.actions = [
    { actionId: 'fixture-history-succeeded', taskId: 4, leadId: 'history-lead-1', platform: 'xhs', action: 'reply_comment', targetUserId: 'fixture-history-buyer-1', noteId: 'history-note-1', commentId: 'history-comment-1', text: '隔离示例：进一步确认交付范围。', accountIdentity: 'xhs:fixture-self', status: 'succeeded', message: '隔离示例：已取得可核验回执', createdAt: now, updatedAt: now + 10, executeCount: 1 },
    { actionId: 'fixture-history-unknown', taskId: 2, leadId: 'history-lead-2', platform: 'douyin', action: 'send_dm', targetUserId: 'fixture-history-buyer-2', noteId: 'history-note-2', text: '隔离示例：确认需求后沟通预算。', accountIdentity: 'douyin:fixture-self', status: 'unknown', error: '提交后连接中断，无法确认是否送达，请先核实，不要重复发送。', createdAt: now - 300, updatedAt: now - 280, executeCount: 1 },
    { actionId: 'fixture-history-failed', taskId: 4, leadId: 'history-lead-3', platform: 'xhs', action: 'comment', targetUserId: 'fixture-history-buyer-3', noteId: 'history-note-3', text: '隔离示例：公开评论草稿。', accountIdentity: 'xhs:fixture-self', status: 'failed', error: '提交前未找到稳定目标，动作未发送。', createdAt: now - 600, updatedAt: now - 590, executeCount: 0 },
  ];
  state.reviews = [{ leadId: 'lead-approved-d', taskId: 4, status: 'approved', note: '已对照原始评论核验本人场景和外包报价意图；预算和周期需要进一步确认。', actor: 1, created: now - 120 }];
  state.prompts.push({ ...clone(state.prompts[2]), id: 10, status: 'disabled', iterationVersion: 'V0.4', notes: '已禁用：候选引用约束不足，保留历史用于审查。' });
  state.feedback.push({ id: 3, taskId: 2, leadId: 'lead-uncertain-b', feedbackType: 'invalid_lead', feedback: '有场景但没有明确委托，应保留待确认。', userNote: '原文需同时包含委托依据。', status: 'analyzed', updatedAt: now - 40, evidence: '团队想做知识库助手，有人能聊聊吗？', analysis: { diagnosis: '把一般讨论误判为具体委托。', target_agents: ['lead_signal_agent'], recommended_action: '创建候选后用固定样本重新评测', prompt_improvements: ['把场景证据和寻求外包证据分开核对'] } }, { id: 4, taskId: 4, leadId: 'lead-promotion-c', feedbackType: 'other', feedback: '供方自述已正确分类，忽略重复建议。', status: 'dismissed', updatedAt: now - 80 });
  return state;
}
function configureFixture(state, body) {
  if (body.reset || body.scenario === 'normal' || body.scenario === 'all-states') Object.assign(state, makeState());
  if (body.scenario === 'empty') {
    Object.assign(state, makeState());
    for (const key of ['tasks', 'templates', 'actions', 'reviews', 'samples', 'evaluations', 'prompts', 'feedback']) state[key] = [];
  }
  if (body.scenario === 'signed-out') { state.loggedIn = false; state.hasAdmin = true; }
  if (body.scenario === 'setup') { state.loggedIn = false; state.hasAdmin = false; }
  for (const key of ['offline', 'failPaths', 'task5CompleteAfter', 'loginError', 'setupError', 'xhsLoginState', 'douyinLoginState', 'outreachOutcome']) if (body[key] !== undefined) state.options[key] = body[key];
  if (body.loggedIn !== undefined) state.loggedIn = body.loggedIn;
  if (body.hasAdmin !== undefined) { state.hasAdmin = body.hasAdmin; if (!body.hasAdmin) state.loggedIn = false; }
  if (body.emptyTasks) state.tasks = [];
  if (body.stopRunningTasks) for (const task of state.tasks) if (task.isRunning) Object.assign(task, { status: 'completed', isRunning: false, statusText: '隔离示例：任务已完成', progress: { percent: 100 } });
  return { options: state.options, loggedIn: state.loggedIn, hasAdmin: state.hasAdmin };
}
function fixtureLogin(state, platform, action, body) {
  const key = `${platform}LoginState`;
  if (action === 'reset') { state.options[key] = 'idle'; return { ok: true, status: 'cleared', message: '隔离示例：已清除模拟状态，未操作真实缓存。' }; }
  if (action === 'start') state.options[key] = platform === 'xhs' && body.mode !== 'browser' ? 'waiting' : 'running';
  const status = state.options[key], authenticated = status === 'authenticated';
  const helper = { status, mode: platform === 'xhs' && !['running'].includes(status) ? 'qrcode' : 'browser', message: '隔离登录演示：没有打开真实平台、扫码或写入账号状态。' };
  if (status === 'waiting') helper.qrImage = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220"><rect width="220" height="220" fill="#f1f5f9"/><rect x="20" y="20" width="180" height="180" rx="8" fill="white" stroke="#cbd5e1"/><text x="110" y="105" text-anchor="middle" font-size="16">扫码区域（模拟）</text><text x="110" y="135" text-anchor="middle" font-size="12">不可扫码 · 无外部连接</text></svg>').toString('base64');
  if (status === 'error' && action === 'confirm') throw new Error('隔离模拟：验证码尚未完成');
  if (action === 'start') return helper;
  const platformResult = platform === 'xhs' ? { ok: authenticated, data: { authenticated, user: authenticated ? { id: 'fixture-only', nickname: '隔离示例账号' } : {} } } : { ok: authenticated, message: authenticated ? '隔离模拟：登录已确认' : '隔离模拟：尚未确认登录' };
  return { helper, [platform]: platformResult, [`${platform}Error`]: status === 'error' ? '隔离模拟：验证码或权限需要人工处理' : '' };
}
function safeFixturePayload(value) {
  if (Array.isArray(value)) return value.map(safeFixturePayload);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /password|api.?key|token/i.test(key) ? '[fixture sensitive field omitted]' : safeFixturePayload(item)]));
}
function routeApi(state, pathname, query, body, method) {
  const taskId = String(body.taskId || query.get('taskId') || '4');
  if (pathname === '/api/auth/me') return { user: { username: 'fixture-admin', role: 'admin' } };
  if (pathname === '/api/auth/login') { if (!state.hasAdmin) throw new Error('请先初始化本机管理员'); if (state.options.loginError) throw new Error(state.options.loginError); state.loggedIn = true; return { user: { username: 'fixture-admin', role: 'admin' }, token: 'fixture-session-only' }; }
  if (pathname === '/api/auth/logout') { state.loggedIn = false; return {}; }
  if (pathname === '/api/setup/status') return { hasAdmin: state.hasAdmin };
  if (pathname === '/api/setup/admin') { if (state.hasAdmin) throw new Error('本机管理员已存在'); if (state.options.setupError) throw new Error(state.options.setupError); if (!body.password || body.password.length < 6) throw new Error('管理密码至少需要6位'); state.hasAdmin = true; state.loggedIn = true; return { user: { username: 'fixture-admin', role: 'admin' } }; }
  if (pathname === '/api/config') return { ...state.settings, collectionDefaults: state.plan };
  if (pathname === '/api/settings') { if (method === 'POST') state.settings = { ...state.settings, ...body }; return state.settings; }
  if (pathname === '/api/collection/defaults') { state.plan = body.plan || body; return state.plan; }
  if (pathname === '/api/templates') { if (method === 'POST') state.templates.push(body); return { templates: state.templates }; }
  if (pathname.endsWith('/tasks/list')) return { tasks: state.tasks.map(({ id, keyword, status, updatedAt, notesCount, leadsCount, confirmedPlan }) => ({ id, keyword, status, updatedAt, notesCount, leadsCount, confirmedPlan })) };
  if (pathname.endsWith('/tasks/status')) { const task = state.tasks.find(t => String(t.id) === taskId); if (task && taskId === '5') { state.statusPolls++; if (state.options.task5CompleteAfter && state.statusPolls >= state.options.task5CompleteAfter) Object.assign(task, { status: 'completed', isRunning: false, statusText: '示例任务已完成', progress: { percent: 100 } }); } return task || { hasTask: false }; }
  if (pathname.endsWith('/tasks/audit')) { const items = [{ id: 1, taskId: Number(taskId), stage: 'lead_signal', decision: 'keep', entityType: 'comment', entityId: 'fixture-comment-1', reason: '原文具有具体场景和委托意图', createdAt: now, metadata: { source: 'fixture' } }].filter(item => (!body.stage || item.stage === body.stage) && (!body.decision || item.decision === body.decision)); return { items, total: items.length, summary: { total: items.length, stages: {} }, hasMore: false }; }
  if (pathname === '/api/search/plan') { state.lastPlan = { ...body, keywords: [...body.seedKeywords, '智能体 外包报价'], keywordReasons: { '智能体 外包报价': '扩大买方委托场景' }, planningMode: 'manual' }; return { planId: 'fixture-plan', plan: state.lastPlan }; }
  if (pathname === '/api/tasks/start') { if (!body.confirmedPlan || body.planId !== 'fixture-plan') throw new Error('须先预览并确认计划'); const task = clone(state.tasks[0] || makeState().tasks[0]); Object.assign(task, { taskId: state.nextTaskId, id: state.nextTaskId++, status: 'completed', isRunning: false, confirmedPlan: body.confirmedPlan, keyword: body.confirmedPlan.keywords.join('，') }); task.result.taskId = task.id; task.result.confirmedPlan = body.confirmedPlan; state.tasks.unshift(task); return task; }
  if (pathname === '/api/tasks/stop') { const task = state.tasks.find(t => String(t.id) === taskId); if (task) Object.assign(task, { status: 'stopped', isRunning: false }); return task || {}; }
  if (pathname === '/api/leads/review') { const task = state.tasks.find(t => String(t.id) === taskId), lead = task?.result.leads.leads.find(l => l.lead_id === body.leadId); if (!lead) throw new Error('候选与任务不匹配'); lead.review = { status: body.status, note: body.note }; state.reviews.push({ ...body, created: now }); return { review: lead.review }; }
  if (pathname === '/api/leads/review-history') return { history: state.reviews.filter(item => item.leadId === query.get('leadId') && (!query.get('taskId') || String(item.taskId) === query.get('taskId'))) };
  if (pathname === '/api/leads/feedback' || pathname === '/api/feedback') { state.feedback.unshift({ id: state.feedback.length + 1, ...body, status: 'pending', createdAt: now }); return { id: state.feedback[0].id, feedbackId: state.feedback[0].id }; }
  if (pathname === '/api/outreach/list') return { actions: state.actions };
  if (pathname === '/api/outreach/draft') return { text: '你好，看到你提到的具体需求，想进一步了解交付范围。（隔离测试草稿）' };
  if (pathname === '/api/outreach/prepare') { if (state.actions.some(action => action.status === 'unknown' && action.targetUserId === body.targetUserId && action.action === body.action)) throw new Error('已有待核实动作，不能重复发送'); const action = { ...body, actionId: `fixture-action-${state.actions.length + 1}`, confirmationToken: 'fixture-confirmation', accountIdentity: 'xhs:fixture-self', status: 'prepared' }; state.actions.unshift(action); return action; }
  if (pathname === '/api/outreach/execute') { const action = state.actions.find(item => item.actionId === body.actionId); if (!action) throw new Error('预览动作不存在'); if (action.status === 'prepared') { if (body.confirmationToken !== action.confirmationToken) throw new Error('缺少匹配的单条确认凭证'); const status = ['unknown', 'failed', 'succeeded'].includes(state.options.outreachOutcome) ? state.options.outreachOutcome : 'unknown'; Object.assign(action, { status, error: status === 'unknown' ? '隔离模拟：提交后回执未确认' : status === 'failed' ? '隔离模拟：提交前失败，动作未发送' : '', executeCount: 1 }); } return action; }
  if (pathname === '/api/outreach/status') return state.actions.find(item => item.actionId === (body.actionId || query.get('actionId'))) || { status: 'unknown' };
  if (pathname === '/api/agents') return { agents: [{ key: 'lead_signal_agent', name: '需求识别', role: '识别原文中的具体场景、买方角色和委托意图', iterationVersion: 'V0.1', promptVersion: 'keyword-v3' }] };
  if (pathname === '/api/prompts') return { prompts: state.prompts.filter(item => !query.get('agentKey') || item.agentKey === query.get('agentKey')) };
  if (pathname === '/api/prompts/promote') { const evaluation = state.evaluations.find(item => item.runId === body.evalRunId); if (!evaluation?.passed || !evaluation.humanReviewComplete || evaluation.split !== 'acceptance' || evaluation.promptId !== Number(body.promptId)) throw new Error('缺少与候选绑定的完整验收'); const candidate = state.prompts.find(item => item.id === Number(body.promptId)); candidate.status = 'active'; return candidate; }
  if (pathname === '/api/prompts/disable') { const candidate = state.prompts.find(item => item.id === Number(body.promptId)); candidate.status = 'disabled'; return candidate; }
  if (pathname === '/api/feedback/list') return { feedback: state.feedback };
  if (pathname.startsWith('/api/feedback/')) { const item = state.feedback.find(item => item.id === Number(body.feedbackId)); if (item) { item.status = pathname.endsWith('/apply') ? 'applied' : pathname.endsWith('/dismiss') ? 'dismissed' : 'analyzed'; if (pathname.endsWith('/apply')) item.candidatePromptId = 9; } return { feedback: item, candidatePromptId: pathname.endsWith('/apply') && item ? item.candidatePromptId : undefined }; }
  if (pathname === '/api/evals/datasets') return query.has('datasetId') ? { datasetId: query.get('datasetId'), samples: query.get('datasetId') === 'dataset-fixture' ? state.samples : [] } : { datasets: state.samples.length ? [{ datasetId: 'dataset-fixture', name: '隔离示例标注集（非真实验收）', count: state.samples.length }] : [] };
  if (pathname === '/api/evals/results') return { runs: state.evaluations };
  if (pathname === '/api/evals/review') { const run = state.evaluations.find(item => item.runId === body.runId); if (!run || !body.noFabrication || !body.citationsVerified || !body.note || run.rows.some(row => !body.reviewedSampleIds?.includes(row.id))) throw new Error('须完整逐条核验'); Object.assign(run, { humanReviewComplete: true, passed: run.autoPassed, humanReview: { note: body.note } }); return run; }
  if (pathname === '/api/evals/run') { const run = { ...clone(state.evaluations[1] || makeState().evaluations[1]), runId: `eval-generated-${state.evaluations.length}`, promptId: Number(body.promptId || 8), split: body.split, passed: false, humanReviewComplete: false }; state.evaluations.unshift(run); return run; }
  if (pathname === '/api/evals/import') return { datasetId: 'dataset-fixture', count: body.samples?.length || 0 };
  const loginRoute = pathname.match(/^\/api\/(xhs|douyin)_login\/(start|status|confirm|reset)$/);
  if (loginRoute) return fixtureLogin(state, loginRoute[1], loginRoute[2], body);
  throw new Error(`Mock API 未定义：${pathname}`);
}
function createServer(state = makeState()) {
  const json = (res, status, body) => { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(body)); };
  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), chunks = []; for await (const chunk of req) chunks.push(chunk);
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};
      if (url.pathname === '/__fixture/status') return json(res, 200, { fixture: true, loggedIn: state.loggedIn, hasAdmin: state.hasAdmin, counts: state.counts, lastPayload: state.lastPayload, options: state.options, actions: state.actions, reviews: state.reviews, tasks: state.tasks.map(({ id, status }) => ({ id, status })) });
      if (url.pathname === '/__fixture/config' && req.method === 'POST') return json(res, 200, { ok: true, ...configureFixture(state, body) });
      if (url.pathname.startsWith('/api/')) {
        state.counts[url.pathname] = (state.counts[url.pathname] || 0) + 1;
        state.lastPayload[url.pathname] = safeFixturePayload(body);
        if (state.options.offline || state.options.failPaths.includes(url.pathname)) return json(res, 503, { ok: false, error: '隔离模拟：连接暂时不可用' });
        if (!state.loggedIn && !['/api/auth/login', '/api/setup/status', '/api/setup/admin'].includes(url.pathname)) return json(res, 401, { ok: false, error: '隔离模拟：会话已过期' });
        try { return json(res, 200, { ok: true, data: routeApi(state, url.pathname, url.searchParams, body, req.method) }); } catch (error) { return json(res, 400, { ok: false, error: error.message }); }
      }
      const name = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1);
      if (!/^[a-zA-Z0-9_.-]+$/.test(name)) return json(res, 404, { error: 'Not found' });
      const file = path.join(assets, name); if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return json(res, 404, { error: 'Not found' });
      const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': `${mime}; charset=utf-8`, 'Cache-Control': 'no-store' }); res.end(fs.readFileSync(file));
    } catch (error) { json(res, 400, { ok: false, error: error.message }); }
  });
}
if (require.main === module) {
  const state = makeState();
  if (process.argv.includes('--check')) {
    assert.deepEqual(state.tasks.map(t => t.id), [4, 2, 5, 3, 1]); assert.equal(state.tasks[0].result.leads.leads.length, 4);
    assert.equal(routeApi(state, '/api/evals/datasets', new URLSearchParams('datasetId=dataset-fixture'), {}, 'GET').samples.length, 2);
    assert.throws(() => routeApi(state, '/api/tasks/start', new URLSearchParams(), {}, 'POST'));
    console.log('PASS: standalone fixture consistency; no business server or network opened.');
  } else {
    const server = createServer(state); server.listen(Number(process.env.PORT || 0), '127.0.0.1', () => console.log(`http://127.0.0.1:${server.address().port}`));
  }
}
module.exports = { makeState, routeApi, createServer, configureFixture, safeFixturePayload };
