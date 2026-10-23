/* Behavioral admin integration in Node VM: no HTTP, model, platform or browser calls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assets = path.resolve(__dirname, '../assets'), html = fs.readFileSync(path.join(assets, 'harness.html'), 'utf8');
const elements = new Map(), timers = new Map(), events = new Map(), requests = [], notices = [];
let timerId = 0, settingsReads = 0, settingsWrites = 0, outstanding = 0, maximumOutstanding = 0;
let route = { page: 'runs', taskId: '4', tab: 'overview' }, settings = { llm: { model: 'saved-model', connect_timeout_s: 30, read_timeout_s: 60, total_timeout_s: 300 }, platforms: { enabled: ['xhs'] } };
let evalRuns = [], promptFixtures = [], reflectionResult = {taskId:2,isEvolving:false,evolution:{status:'awaiting_evaluation',candidatePromptIds:[11]}}, reviewResult = {};
let tasks = [], statusMode = 'complete', resolvers = [], confirmAnswer = true;
let feedbackMode = 'complete', feedbackRecords = [];
const plain = value => JSON.parse(JSON.stringify(value)), flush = () => new Promise(setImmediate);
function element(id) {
  if (!elements.has(id)) { let value = ''; const el = { id, checked: false, disabled: false, hidden: false, textContent: '', innerHTML: '', dataset: {}, style: {}, open: false, isConnected: true,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, attributes: new Map(),
    setAttribute(name, value) { this.attributes.set(name, String(value)); }, getAttribute(name) { return this.attributes.get(name); }, removeAttribute(name) { this.attributes.delete(name); },
    addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; }, focus() {}, scrollIntoView() {},
  }; Object.defineProperty(el, 'value', { get: () => value, set: next => { value = String(next); } }); elements.set(id, el); }
  return elements.get(id);
}
for (const match of html.matchAll(/<(input|select|textarea)[^>]*id="([^"]+)"[^>]*>/g)) {
  const el = element(match[2]); el.type = match[0].match(/type="([^"]*)"/)?.[1] || (match[1] === 'input' ? 'text' : match[1]); el.value = match[0].match(/value="([^"]*)"/)?.[1] || ''; el.checked = /\schecked\b/.test(match[0]);
  if (match[1] === 'select') el.value = html.slice(match.index).match(/<option value="([^"]*)"/)?.[1] || '';
}
const sandbox = {
  console, Date, URL, URLSearchParams, innerWidth: 1440, location: { protocol: 'http:', origin: 'http://fixture.invalid', hash: '#/runs?task=4' },
  document: { hidden: false, getElementById: element, querySelectorAll: selector => selector.includes('#page-settings') ? [...elements.values()].filter(el => el.id.startsWith('setting') && ['text', 'number', 'checkbox', 'password', 'select'].includes(el.type)) : [], querySelector(selector) { return selector.startsWith('.workspace-sidebar') ? element('fixture-sidebar') : null; }, addEventListener(name, fn) { events.set(name, fn); } },
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  WorkspaceUI: { route: () => route, navigate(page, fields) { route = { page, ...fields }; }, confirm: async () => confirmAnswer, notify: message => notices.push(message), showDialog: el => { el.open = true; }, closeDialog: el => { el.open = false; }, icon: () => '' },
  addEventListener() {}, alert: message => notices.push(message),
  setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); },
  fetch() { throw new Error('Production network is forbidden in admin VM tests'); },
};
sandbox.window = sandbox;
const context = vm.createContext(sandbox), run = source => vm.runInContext(source, context);
require('./ui-libraries.cjs').loadUiLibraries(context, path.resolve(__dirname, '../assets'));
for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) run(script[1]);
for (const name of ['harness-evaluations.js', 'harness-workspace.js']) vm.runInContext(fs.readFileSync(path.join(assets, name), 'utf8'), context, { filename: name });
sandbox.mockGet = async url => {
  requests.push({ url, method: 'GET' });
  if (url === '/api/settings') { settingsReads++; return cloneSettings(); }
  if (url.startsWith('/api/agents/call?')) return {callId:'call-1',status:'completed',input:{text:'<script>saved</script>'},request:{model:'fixture'},output:{summary:'stored output'},metrics:{durationMs:10}};
  if (url.includes('/api/feedback/list')) {
    if (feedbackMode === 'fail') throw new Error('fixture feedback disconnected');
    return { feedback: plain(feedbackRecords) };
  }
  return url.includes('datasets') ? { datasets: [] } : url.includes('results') ? { runs: evalRuns } : url.includes('prompts') ? { prompts: promptFixtures } : url.includes('feedback') ? { feedback: [] } : { agents: [] };
};
const cloneSettings = () => plain(settings);
sandbox.mockPost = async (url, body) => {
  requests.push({ url, body: plain(body) });
  if (url === '/api/admin/tasks/list') return { tasks };
  if (url === '/api/evolution/reflect') return reflectionResult;
  if (url === '/api/evals/review') return reviewResult;
  if (url === '/api/prompts/rollback') return {activePrompt:promptFixtures.find(item => item.id === body.promptId)};
  if (url === '/api/settings') { settingsWrites++; settings = plain(body); return {}; }
  if (url === '/api/admin/tasks/audit') return { items: [], total: 0 };
  if (url === '/api/admin/tasks/status') {
    outstanding++; maximumOutstanding = Math.max(maximumOutstanding, outstanding);
    try {
      if (statusMode === 'defer') return await new Promise(resolve => resolvers.push({ taskId: body.taskId, resolve }));
      if (statusMode === 'fail') throw new Error('fixture disconnected');
      return { taskId: body.taskId, status: statusMode === 'running' ? 'running' : 'completed', isRunning: statusMode === 'running', updatedAt: 1792555200, result: {} };
    } finally { outstanding--; }
  }
  throw new Error(`Unmocked route ${url}`);
};
run('adminReady = true; get = mockGet; post = mockPost;');
(async () => {
  await run('loadSettings(true)'); assert.equal(settingsReads, 1);
  element('settingModel').value = 'unsaved-draft'; run('markAdminSettingsDirty()');
  assert.equal(run('adminSettingsDirty'), true); assert.match(element('settingsDirtyStatus').textContent, /未保存/);
  settings.llm.model = 'server-changed'; await run('loadSettings(true)');
  assert.equal(settingsReads, 1, 'even forced reload must preserve a dirty settings draft');
  assert.equal(element('settingModel').value, 'unsaved-draft');
  await run('refreshAll()'); assert.equal(element('settingModel').value, 'unsaved-draft');
  assert.equal(run('adminSettingsDirty'), true);

  tasks = [{ id: 5, status: 'running' }]; await run('saveSettings()');
  assert.equal(settingsWrites, 0, 'running tasks lock setting mutation before POST');
  assert.equal(element('saveSettingsBtn').disabled, true); assert.equal(element('settingModel').value, 'unsaved-draft');
  tasks = []; await run('saveSettings()');
  assert.equal(settingsWrites, 1); assert.equal(settings.llm.model, 'unsaved-draft'); assert.equal(run('adminSettingsDirty'), false);
  element('settingReadTimeout').value = '1.5'; run('markAdminSettingsDirty()'); await run('saveSettings()');
  assert.equal(settingsWrites, 1); assert.equal(run('adminSettingsDirty'), true); assert.ok(notices.some(message => /1–86400/.test(message)));
  confirmAnswer = false; await run('discardAdminSettings()'); assert.equal(element('settingReadTimeout').value, '1.5');
  confirmAnswer = true; await run('discardAdminSettings()'); assert.equal(Number(element('settingReadTimeout').value), 60); assert.equal(run('adminSettingsDirty'), false);

  // A task switch queues the next status read. The slower old response must not
  // replace the selected task or overwrite the audit task field.
  statusMode = 'defer'; run("currentTaskId = '4'; latestTask = null;");
  const oldRequest = run("loadTask('4')"); await flush(); assert.equal(resolvers.length, 1);
  route = { page: 'runs', taskId: '2', tab: 'overview' }; run('applyAdminRoute()');
  assert.equal(element('auditTaskId').value, '2'); assert.equal(resolvers.length, 1, 'switch does not create parallel status calls');
  resolvers.shift().resolve({ taskId: '4', status: 'running', isRunning: true, result: {} }); await flush();
  assert.equal(resolvers.length, 1); assert.equal(resolvers[0].taskId, '2'); assert.equal(run('latestTask'), null);
  resolvers.shift().resolve({ taskId: '2', status: 'completed', isRunning: false, result: {} }); await oldRequest;
  assert.equal(run('latestTask.taskId'), '2'); assert.equal(maximumOutstanding, 1); assert.equal(run('adminPollTimer'), null);
  assert.match(element('workbenchLink').href, /task=2/);

  statusMode = 'running'; await run("loadTask('2')");
  const scheduled = timers.get(run('adminPollTimer')); assert.equal(scheduled.delay, 2000);
  const lastSync = run('adminLastSyncedAt'); statusMode = 'fail'; await run("loadTask('2')");
  assert.equal(run('latestTask.taskId'), '2'); assert.equal(run('adminLastSyncedAt'), lastSync);
  assert.match(element('adminSyncStatus').textContent, /同步失败.*保留上次数据/);
  sandbox.document.hidden = true; events.get('visibilitychange')(); assert.equal(run('adminPollTimer'), null);
  statusMode = 'complete'; sandbox.document.hidden = false; events.get('visibilitychange')(); await run('adminStatusRequest');
  assert.equal(run('latestTask.isRunning'), false); assert.equal(run('adminPollTimer'), null);

  route = { page: 'runs', taskId: '2', tab: 'audit' }; run('applyAdminRoute()'); await flush();
  const audit = requests.filter(req => req.url === '/api/admin/tasks/audit').at(-1);
  assert.equal(audit.body.taskId, 2, 'audit always follows selected task even if the field was stale');
  assert.equal(requests.filter(req => req.url.includes('/api/tasks/')).length, 0, 'admin reads stay in the admin API namespace');
  sandbox.traceFixture = { taskId: '2', status: 'completed', agentTrace: [{ agentKey: 'lead_signal_agent', inputSummary: { text: '<script>fixture</script>' }, outputSummary: { summary: '已核对两条引用' }, durationMs: 1200 }, { agentKey: 'relevance_agent', outputSummary: '旧记录仅保存结论' }] };
  run('renderTask(traceFixture)');
  assert.match(element('trace').innerHTML, /调用输入.*已保存摘要/);
  assert.match(element('trace').innerHTML, /&lt;script&gt;fixture&lt;\/script&gt;/);
  assert.match(element('trace').innerHTML, /未保存输入摘要/);
  assert.doesNotMatch(element('trace').innerHTML, /<script>|<details[^>]*\sopen(?:\s|>)/, 'diagnostic input/output must be escaped and collapsed by default');
  element('adminFeedbackFilter').value = '';
  sandbox.feedbackFixture = [{ id: 1, status: 'applied', candidatePromptId: 8 }, { id: 2, status: 'applied', candidate_prompt_id: 9 }];
  const writesBeforeFeedbackRender = requests.length;
  run('renderFeedback(feedbackFixture)');
  assert.match(element('feedbackQueue').innerHTML, /openCandidateDetail\(8\)/);
  assert.match(element('feedbackQueue').innerHTML, /openCandidateDetail\(9\)/);
  assert.equal(requests.length, writesBeforeFeedbackRender, 'rendering related candidates never mutates the service');
  for (const id of [undefined, '', 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, '1);alert(1)//']) {
    sandbox.feedbackFixture = [{ id: 1, status: 'applied', candidatePromptId: id }]; run('renderFeedback(feedbackFixture)');
    assert.doesNotMatch(element('feedbackQueue').innerHTML, /openCandidateDetail|查看关联候选/, 'only positive safe candidate IDs become interactive links');
  }
  // A failed initial read is an error, not an empty queue; later failures retain
  // the last successful records and propagate into the overall refresh state.
  feedbackMode = 'fail'; run('feedbackLoaded = false; feedbackLoadError = false; latestFeedback = [];');
  await assert.rejects(run('loadFeedback()'), /fixture feedback disconnected/);
  assert.equal(element('feedbackSyncStatus').hidden, false);
  assert.match(element('feedbackSyncMessage').textContent, /无法加载.*刷新重试/);
  assert.equal(element('feedbackQueue').innerHTML, '');
  run('renderFeedback(latestFeedback)');
  assert.equal(element('feedbackQueue').innerHTML, '', 'changing a filter after a first-load error must not claim the queue is empty');
  feedbackMode = 'complete'; feedbackRecords = [{ id: 31, feedback: '保留这条旧反馈', status: 'applied', candidatePromptId: 8 }];
  await run('loadFeedback()');
  const feedbackBeforeFailure = element('feedbackQueue').innerHTML;
  assert.equal(element('feedbackSyncStatus').hidden, true); assert.equal(run('feedbackLoadError'), false);
  feedbackMode = 'fail';
  await assert.rejects(run('loadFeedback()'), /fixture feedback disconnected/);
  assert.equal(element('feedbackQueue').innerHTML, feedbackBeforeFailure);
  assert.equal(run('latestFeedback[0].id'), 31);
  assert.match(element('feedbackSyncMessage').textContent, /以下为上次加载的数据.*检查连接后刷新重试/);
  route = { page: 'feedback', taskId: '2' }; run("activePage = 'feedback';");
  await run('refreshAll()');
  assert.match(element('adminSyncStatus').textContent, /同步失败/);
  assert.equal(element('feedbackQueue').innerHTML, feedbackBeforeFailure);
  await run("loadTask('2')");
  assert.match(element('adminSyncStatus').textContent, /反馈队列同步失败/, 'a successful task poll must not hide the stale feedback warning');
  feedbackMode = 'complete'; feedbackRecords = [{ id: 32, feedback: '恢复连接后的新反馈', status: 'pending' }];
  await run('refreshAll()');
  assert.equal(element('feedbackSyncStatus').hidden, true); assert.equal(run('feedbackLoadError'), false);
  assert.match(element('feedbackQueue').innerHTML, /恢复连接后的新反馈/);
  assert.doesNotMatch(element('feedbackQueue').innerHTML, /保留这条旧反馈/);
  assert.doesNotMatch(element('adminSyncStatus').textContent, /同步失败/);
  sandbox.collectionFixture={taskId:'5',status:'partial',isRunning:false,result:{notes:[],collectionSummary:{filterCounts:{unknown_publish_time:7,duplicate:0},timeResolution:{attempted:9,resolved:2,unresolved:7,failed:1},timeReferenceAt:1792555200,zeroResultReason:'7 条发布时间待核验'}}};
  run('renderTask(collectionFixture)');
  assert.match(element('harnessStageCoverage').innerHTML,/采集过滤、正文与发布时间核验/);
  assert.match(element('harnessStageCoverage').innerHTML,/发布时间仍待核验 <b>7/);
  assert.match(element('harnessStageCoverage').innerHTML,/额外补读详情 9/);
  assert.match(element('harnessStageCoverage').innerHTML,/无结果说明：.*7 条发布时间待核验/);
  run("renderTask({taskId:'5',status:'completed',result:{notes:[],collectionSummary:{rawSeen:122}}})");
  assert.match(element('harnessStageCoverage').innerHTML,/旧任务未记录过滤明细/);
  assert.doesNotMatch(element('harnessStageCoverage').innerHTML,/发布时间仍待核验 <b>7|122/,'admin task switches must not retain another task’s filtering reasons');
  // Completed execution, QA findings and human review are independent.
  sandbox.stateFixture = {taskId:'2',status:'completed',stageCoverage:{qa:{status:'completed'}},qa:{approved:false},leads:{leads:[{qa_status:'needs_review',review:{status:'pending'}}]},evolution:{status:'not_started'}};
  run('latestTask=stateFixture; renderTask(stateFixture)');
  assert.match(element('adminTaskInsights').innerHTML,/执行已完成/); assert.match(element('adminTaskInsights').innerHTML,/质检需复核/); assert.match(element('adminTaskInsights').innerHTML,/1 条待人工审核/);
  const beforeReflect = requests.filter(req=>req.url==='/api/evolution/reflect').length;
  assert.equal(beforeReflect,0,'read-only rendering must never initiate model reflection');
  confirmAnswer=false; await run('reflectCurrentTask()'); assert.equal(requests.filter(req=>req.url==='/api/evolution/reflect').length,0);
  confirmAnswer=true; await run('reflectCurrentTask()'); assert.deepEqual(requests.find(req=>req.url==='/api/evolution/reflect').body,{taskId:2});
  run("latestTask={taskId:'2',status:'completed',isRunning:false,isEvolving:true}; currentTaskId='2'; scheduleAdminPoll()");
  assert.equal(timers.get(run('adminPollTimer')).delay,2000,'evolution keeps read-only polling after execution has finished');
  assert.equal(run('hasAdminRunningTask()'),true,'evolution prevents settings and version changes');
  run('latestTask.isEvolving=false; scheduleAdminPoll()');

  run("latestTask={taskId:'2',isRunning:false,agentTrace:[{agentKey:'critic_qa_agent',callId:'call-1',hasFullAudit:true,inputSummaryTruncated:true}]}; renderTask(latestTask)");
  assert.match(element('trace').innerHTML,/查看完整输入输出/); assert.match(element('trace').innerHTML,/有截断/);
  const requestsBeforeFullAudit=requests.length; await run('openAgentCall(0)');
  assert.match(requests.at(-1).url,/\/api\/agents\/call\?taskId=2&callId=call-1/); assert.equal(requests.at(-1).method,'GET');
  assert.match(element('agentCallBody').innerHTML,/&lt;script&gt;saved/); assert.doesNotMatch(element('agentCallBody').innerHTML,/<script>/);
  assert.equal(requests.length,requestsBeforeFullAudit+1,'full call inspection is a single read, not a replay');
  run('closeAgentCall()'); assert.equal(element('agentCallDialog').open,false);
  run("latestTask={taskId:'2',status:'completed',isRunning:false,evolution:{status:'interrupted'}}");
  await run('reflectCurrentTask()'); assert.equal(requests.filter(req=>req.url==='/api/evolution/reflect').at(-1).body.retry,true);

  // Specialised QA/report acceptance needs independent per-row quality decisions.
  sandbox.qaRun = {runId:'qa-run',datasetId:'qa-set',promptId:11,agentKey:'critic_qa_agent',evaluationKind:'qa',split:'acceptance',autoPassed:true,passed:false,humanReviewComplete:false,requiredHumanChecks:['问题归因与严重度正确'],metrics:{machineChecksPassed:1,failedSamples:0},rows:[{id:'qa-1',inputText:'真实原文',inputContext:{leads:[{id:'a'}]},expected:{approved:false,issueTargets:['a']},prediction:{approved:false,issues:[{target_id:'a',level:'warning'}]},machineChecksPassed:true,citationPassed:null,fabricationPassed:null}]};
  await run('displayEvaluationResult(qaRun)');
  assert.match(element('evalResultSummary').innerHTML,/质检专用评测/); assert.doesNotMatch(element('evalResultSummary').innerHTML,/相关性精确率/);
  assert.match(element('evalComparison').innerHTML,/完整冻结输入与人工预期/); assert.match(element('evalComparison').innerHTML,/本条人工质量结论/); assert.match(element('evalComparison').innerHTML,/引用与事实仍须人工核验/);
  run('markEvaluationRow(0,true)'); for(const id of ['evalReviewedAll','evalNoFabrication','evalCitationsVerified']) element(id).checked=true;
  element('evalHumanNote').value='已核对原文与问题归因';
  await run('confirmEvaluationReview()'); assert.equal(requests.filter(req=>req.url==='/api/evals/review').length,0,'check-all never replaces per-row semantic review');
  run("setEvaluationQuality(0,'decision','fail'); setEvaluationQuality(0,'note','问题存在，但严重度判断不准确')");
  reviewResult={...plain(sandbox.qaRun),humanReviewComplete:true,qualityReviewComplete:true,passed:false};
  await run('confirmEvaluationReview()');
  const savedReview=requests.find(req=>req.url==='/api/evals/review');
  assert.deepEqual(savedReview.body.qualityReviews,[{sampleId:'qa-1',passed:false,note:'问题存在，但严重度判断不准确'}]);
  assert.match(element('evalStatus').textContent,/不能启用候选/); assert.equal(run('evaluationState(currentEvaluationResult)'),'验收未通过');
  sandbox.otherRun={...plain(sandbox.qaRun),runId:'other-run'}; await run('displayEvaluationResult(otherRun)'); assert.equal(run('evaluationQualityReviews.size'),0,'row decisions never leak between runs');
  promptFixtures=[{id:11,agentKey:'critic_qa_agent',status:'candidate',prompt:'candidate'}, {id:12,agentKey:'critic_qa_agent',status:'active',prompt:'active',activatedAt:1792555200}, {id:13,agentKey:'critic_qa_agent',status:'candidate',prompt:'historic',activatedAt:1792455200}];
  await run('loadPrompts()'); run('openCandidateDetail(11,false)'); assert.match(element('candidateBody').innerHTML,/为此候选创建评测/); assert.doesNotMatch(element('candidateBody').innerHTML,/rollbackPrompt\(11\)/);
  run('openCandidateDetail(13,false)'); assert.match(element('candidateBody').innerHTML,/回退到此历史版本/);
  assert.equal(run("evaluationEligibleForPromotion({agentKey:'critic_qa_agent',passed:true,humanReviewComplete:true,split:'acceptance'})"),false,'specialised promotion still requires per-row quality proof');
  assert.equal(run("evaluationEligibleForPromotion({agentKey:'critic_qa_agent',passed:true,humanReviewComplete:true,qualityReviewComplete:true,split:'acceptance'})"),true);
  const rollbackBefore=requests.filter(req=>req.url==='/api/prompts/rollback').length;
  await run('rollbackPrompt(11)'); assert.equal(requests.filter(req=>req.url==='/api/prompts/rollback').length,rollbackBefore,'an unevaluated candidate cannot use rollback');
  confirmAnswer=false; await run('rollbackPrompt(13)'); assert.equal(requests.filter(req=>req.url==='/api/prompts/rollback').length,rollbackBefore);
  confirmAnswer=true; await run('rollbackPrompt(13)'); assert.deepEqual(requests.filter(req=>req.url==='/api/prompts/rollback').at(-1).body,{promptId:13,agentKey:'critic_qa_agent'});
  // Everyday labels and generated summaries are Chinese; stored code, version
  // identifiers and quoted user material must remain exactly inspectable.
  const staticText = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>|<style\b[^>]*>[\s\S]*?<\/style>|<pre\b[^>]*>[\s\S]*?<\/pre>/g,'').replace(/<[^>]+>/g,'');
  assert.doesNotMatch(staticText, /\b(?:Agent|Prompt|QA)\b/);
  assert.equal(run("statusLabel('partial')"), '部分完成');
  assert.doesNotMatch(run("agentLabel('critic_qa_agent') + stageLabel('lead_signal') + purposeLabel('batch note relevance')"), /critic_qa_agent|lead_signal|batch note relevance|Agent|QA/);
  assert.doesNotMatch(run("adminTraceSummary({decision:'needs_review'})"), /needs_review/);
  assert.doesNotMatch(run("adminTraceSummary({decision:'new_internal_decision'})"), /new_internal_decision/);
  assert.doesNotMatch(run("adminTraceSummary({summary:{stage:'qa',status:'failed',reason:'total_timeout'}})"), /\[object Object\]|total_timeout|\"stage\"|\"status\"/);
  sandbox.localeTask = {taskId:'2',status:'partial',agentTrace:[{agentKey:'critic_qa_agent',purpose:'quality and safety review',runType:'main',promptVersion:'v2.7-test',error:'total_timeout',outputSummary:{decision:'needs_review'},inputSummary:{text:'User Agent / Prompt / QA',model:'deepseek-v4.1-flash-1017'}}],events:[{level:'info',agent:'Model Usage',message:'历史用量通知'},{level:'info',agent:'模型用量',message:'另一条历史用量通知'},{level:'info',agent:'Model Request',message:'开始请求模型。'},{level:'warning',agent:'Model Request',message:'正在重试。'},{level:'error',agent:'Model Request',message:'total_timeout'}]};
  run('renderTask(localeTask)');
  assert.match(element('trace').innerHTML,/v2\.7-test/);
  assert.match(element('trace').innerHTML,/User Agent \/ Prompt \/ QA/);
  assert.match(element('trace').innerHTML,/deepseek-v4\.1-flash-1017/);
  const readableTrace = element('trace').innerHTML.replace(/<pre>[\s\S]*?<\/pre>/g,'').replace(/<[^>]+>/g,'');
  assert.doesNotMatch(readableTrace,/total_timeout|needs_review|quality and safety review|\bmain\b|critic_qa_agent/);
  assert.doesNotMatch(element('events').innerHTML,/Model Usage|模型用量|历史用量通知/,'historical usage events are omitted from ordinary execution logs');
  assert.match(element('events').innerHTML,/信息<\/span>/);
  assert.match(element('events').innerHTML,/警告<\/span>/);
  assert.match(element('events').innerHTML,/错误<\/span>/);
  assert.equal((element('events').innerHTML.match(/class="event"/g) || []).length,3);
  assert.equal(sandbox.localeTask.events.length,5,'filtering the timeline must not mutate stored event data');
  sandbox.localeAudit = [{taskId:2,stage:'details',decision:'unknown_publish_time',entityType:'note',entityId:'note-id',message:'unknown_publish_time',evidence:'User QA / Agent / Prompt',sourceKeyword:'Prompt API',runType:'main'}];
  run('renderAuditLogs(localeAudit,{total:1})');
  const auditReason = element('auditLogs').innerHTML.match(/原因：([^<]*)/)?.[1];
  assert.ok(auditReason); assert.doesNotMatch(auditReason,/unknown_publish_time/);
  assert.match(element('auditLogs').innerHTML,/User QA \/ Agent \/ Prompt/);
  assert.match(element('auditLogs').innerHTML,/关键词：Prompt API/);
  assert.match(element('auditLogs').innerHTML,/unknown_publish_time/,'the collapsed original data keeps exact field values');
  sandbox.localeEval = {...plain(sandbox.qaRun),rows:[{id:'quoted-source',inputText:'User Prompt Agent QA',prediction:{summary:{status:'needs_review',reason:'unknown_publish_time'}},error:'stream_idle_timeout',evidence:['Original Prompt Agent QA']} ]};
  run('renderEvaluationRows(localeEval,[])');
  assert.match(element('evalComparison').innerHTML,/User Prompt Agent QA/);
  assert.match(element('evalComparison').innerHTML,/Original Prompt Agent QA/);
  const evaluationError = element('evalComparison').innerHTML.match(/class="admin-error">([^<]*)/)?.[1];
  assert.ok(evaluationError); assert.doesNotMatch(evaluationError,/stream_idle_timeout/);
  // Escaping preserves source evidence and prompt text exactly, including former brand words.
  const originalEvidence='微闻云的销售团队和内部销售、销售人员需要 <b>原文</b>';
  sandbox.originalEvidence=originalEvidence;
  assert.equal(run('esc(originalEvidence)'),'微闻云的销售团队和内部销售、销售人员需要 &lt;b&gt;原文&lt;/b&gt;');
  sandbox.rawEvidenceEval={...plain(sandbox.qaRun),rows:[{id:'evidence',inputText:originalEvidence,evidence:[originalEvidence],prediction:{summary:'已完成'}}]};
  run('renderEvaluationRows(rawEvidenceEval,[])');
  assert.match(element('evalComparison').innerHTML,/微闻云的销售团队和内部销售、销售人员需要 &lt;b&gt;原文&lt;\/b&gt;/);
  const controller=fs.readFileSync(path.join(assets,'harness-workspace.js'),'utf8');
  assert.doesNotMatch(controller,/adminOriginal|adminBaseNotice|^\w+ = (async )?function/m);
  assert.match(html, /<dialog id="candidateDialog"/);
  assert.match(html, /<dialog id="agentCallDialog"/);
  assert.doesNotMatch(html,/promptModalMask|settingDelayMin|settingDelayMax|settingMaxComments|settingDouyinMaxComments/);
  let traceWrites=0, traceHtml=element('trace').innerHTML;
  Object.defineProperty(element('trace'),'innerHTML',{configurable:true,get:()=>traceHtml,set:value=>{traceWrites++;traceHtml=value;}});
  run('renderTask(localeTask)'); assert.equal(traceWrites,1,'one task refresh renders its trace exactly once');
  // The submitted payload, rather than the response-time form, defines what was saved.
  tasks=[]; run('adminTasks=[];latestTask=null;adminReflectionTaskId="";adminSettingsDirty=false;settingsLoaded=false;');
  await run('loadSettings(true)');
  const originalPost=run('post'); let releaseSave, submitted;
  sandbox.delaySettings=async(url,body)=>{
    if(url==='/api/admin/tasks/list') return {tasks:[]};
    if(url==='/api/settings') {submitted=plain(body);return await new Promise(resolve=>releaseSave=resolve);}
    throw new Error('Unexpected request '+url);
  };
  run('post=delaySettings'); element('settingModel').value='model-A';element('settingApiKey').value='test-key-A';run('markAdminSettingsDirty()');
  const save=run('saveSettings()');await flush();
  assert.equal(element('settingsModelGroup').disabled,true,'inputs are locked during save');
  assert.equal(submitted.llm.model,'model-A');
  assert.deepEqual(Object.keys(submitted.xhs).sort(),['bin','enable_follow_actions']);
  assert.deepEqual(Object.keys(submitted.douyin).sort(),['enable_dm_actions','enable_follow_actions']);
  // A programmatic update/new key must also survive the response even though UI inputs are locked.
  element('settingModel').value='model-B';element('settingApiKey').value='test-key-B';run('markAdminSettingsDirty()');
  releaseSave({});await save;
  assert.equal(run('adminSettingsDirty'),true);assert.equal(element('settingApiKey').value,'test-key-B');
  assert.match(element('settingsDirtyStatus').textContent,/未保存/);assert.match(element('settingsNotice').textContent,/之后的修改仍未保存/);
  const secondSave=run('saveSettings()');await flush();assert.equal(submitted.llm.model,'model-B');
  releaseSave({});await secondSave;
  assert.equal(run('adminSettingsDirty'),false);assert.equal(element('settingApiKey').value,'');
  assert.equal(element('settingsModelGroup').disabled,false);sandbox.post=originalPost;
  console.log('PASS: admin workflows, safe task scope, Chinese status/summary/error labels, hidden usage reminders, and unchanged technical records/user evidence.');
})().catch(error => { console.error(error); process.exitCode = 1; });
