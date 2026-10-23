/* Workbench composition contracts in a memory DOM. Full layout is checked separately in CUA. */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assets = path.resolve(__dirname, '../assets'), html = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
const elements = new Map(), created = [], requests = [], notices = [], timers = new Map();
let initializing = false, timerId = 0, route = { page: 'tasks', taskId: '', tab: '' }, responseFor = () => ({}), active = null, sourcePanel = null;
const plain = value => JSON.parse(JSON.stringify(value)), flush = () => new Promise(setImmediate);
function node(id, tagName = 'DIV') {
  let value = '';
  const classes = new Set(), attributes = new Map(), listeners = new Map();
  const el = { id, tagName, dataset: {}, style: {}, hidden: false, checked: false, disabled: false, textContent: '', innerHTML: '', children: [], previousElementSibling: { textContent: id }, selectedOptions: [{ textContent: '默认' }], isConnected: true,
    classList: { add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)), contains: name => classes.has(name), toggle(name, force) { const yes = force ?? !classes.has(name); yes ? classes.add(name) : classes.delete(name); return yes; } },
    setAttribute: (name, val) => attributes.set(name, String(val)), getAttribute: name => attributes.get(name), removeAttribute: name => attributes.delete(name),
    addEventListener(name, listener) { listeners.set(name, listener); }, fire(name) { return listeners.get(name)?.({ target: el }); },
    append(...children) { this.children.push(...children); }, prepend(...children) { this.children.unshift(...children); }, before() {}, after() {}, focus() { active = this; }, scrollIntoView() {},
    contains: target => target === el || (id === 'output' && target?.id?.startsWith('review')),
    closest: selector => selector === '.lead-actions' && id.startsWith('review') ? element('review-actions') : null,
    querySelector(selector) { return this.queries?.[selector] || null; }, querySelectorAll(selector) { return this.collections?.[selector] || []; },
  };
  Object.defineProperty(el, 'value', { get: () => value, set: next => { value = String(next); } });
  return el;
}
function element(id) { if (!elements.has(id)) elements.set(id, node(id)); return elements.get(id); }
for (const match of html.matchAll(/<(input|select|textarea)[^>]*id="([^"]+)"[^>]*>/g)) {
  const el = element(match[2]); el.tagName = match[1].toUpperCase(); el.value = match[0].match(/value="([^"]*)"/)?.[1] || ''; el.checked = /\schecked\b/.test(match[0]);
  if (match[1] === 'select') el.value = html.slice(match.index).match(/<option value="([^"]*)"/)?.[1] || '';
  if (match[1] === 'textarea') el.value = html.slice(match.index + match[0].length).split('</textarea>')[0];
}
const storage = () => { const data = new Map(); return { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) }; };
const steps = [1, 2, 3].map(number => { const step = node(`step-${number}`); step.dataset.wbStep = String(number); step.queries = { button: node(`step-button-${number}`, 'BUTTON') }; return step; });
const sandbox = { console, URL, URLSearchParams, Date, location: { protocol: 'http:', origin: 'http://fixture.invalid', hash: '' }, innerWidth: 1440,
  document: { hidden: false, get activeElement() { return active; }, getElementById: id => initializing && id === 'salesMain' ? null : element(id),
    querySelector(selector) { if (selector === '[data-result-panel="leads"]') return sourcePanel; if (selector === '[data-result-tab].active') return { dataset: { resultTab: 'leads' } }; if (selector === 'label[for="exportScope"]') return element('exportLabel'); if (selector === '.wb-menu-button') return element('menu-button'); if (selector === '.wb-frame') return element('main-frame'); if (selector === '.wb-sidebar') return element('wbSidebar'); return null; }, querySelectorAll(selector) { return selector === '[data-wb-step]' ? steps : []; }, addEventListener() {},
    createElement(tag) { const el = node(`created-${created.length}`, tag.toUpperCase()); created.push(el); return el; }, body: { append() {} },
  },
  localStorage: storage(), sessionStorage: storage(), navigator: {}, addEventListener() {}, alert: message => notices.push(message),
  WorkspaceUI: { wireButtonFeedback() {}, route: () => route, navigate(page, params) { route = { page, ...params }; }, notify: message => notices.push(message), showDialog: el => { el.style.display = 'flex'; }, closeDialog: el => { el.style.display = 'none'; }, icon: () => '' },
  setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id), setInterval() { throw new Error('New task polling must not use intervals'); }, clearInterval() {},
  fetch: async (url, options = {}) => { const request = { url, body: options.body ? JSON.parse(options.body) : null }; requests.push(request); const data = await responseFor(request); return { status: 200, ok: true, json: async () => ({ ok: true, data }) }; },
};
sandbox.window = sandbox;
const context = vm.createContext(sandbox), run = source => vm.runInContext(source, context);
require('./ui-libraries.cjs').loadUiLibraries(context, path.resolve(__dirname, '../assets'));
for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) run(script[1]);
run(fs.readFileSync(path.join(assets, 'workbench.js'), 'utf8'));
// The shell layout is exercised by the real browser. Mount only its existing
// business-facing nodes here, so the VM can target state transitions precisely.
initializing = true; run(fs.readFileSync(path.join(assets, 'workbench-workspace.js'), 'utf8')); initializing = false;
element('wbConfiguration').queries = { '.config-grid': element('config-grid'), '.wb-boundary-note': element('boundary') };
element('wbLeadDetail').queries = { '.wb-detail-empty': element('detail-empty') };
element('taskPanel').queries = { '.wb-progress-disclosure': element('progress-disclosure') };
const workspace = sandbox.WorkbenchWorkspace;
(async () => {
  route = { page: 'new' };
  run("setStatus('上一次完成任务', '✅'); syncTaskStatus({status:'completed',isRunning:false}, '已恢复任务 #4：')");
  assert.match(element('status').innerHTML, /尚未开始任务/); assert.match(element('status').className, /status-idle/);
  run("setStatus('新建页状态'); syncTaskStatus({status:'completed',isRunning:false})");
  assert.match(element('status').innerHTML, /尚未开始任务/, 'completed status has no special exemption when its restore prefix is absent');
  for (const icon of ['❌', '⏳']) {
    sandbox.statusIcon = icon; run("setStatus('当前表单的重要状态', statusIcon); syncTaskStatus({status:'completed',isRunning:false}, '已恢复任务 #4：')");
    assert.match(element('status').innerHTML, /当前表单的重要状态/, 'background restoration must preserve foreground errors/running feedback');
  }
  run("activeSearchPlan={}; setStatus('计划已生成', '✅'); syncTaskStatus({status:'completed',isRunning:false}, '已恢复任务 #4：')");
  assert.match(element('status').innerHTML, /计划已生成/); run('activeSearchPlan=null');
  element('searchBtn').disabled = true;
  run("setStatus('正在提交计划'); syncTaskStatus({status:'completed',isRunning:false}, '已恢复任务 #4：')");
  assert.match(element('status').innerHTML, /正在提交计划/); element('searchBtn').disabled = false;
  run("syncTaskStatus({status:'partial',isRunning:false}, '已恢复任务 #2：')");
  assert.match(element('status').innerHTML, /部分完成/, 'a partial restored task is still important state, not idle');
  run("syncTaskStatus({status:'running',isRunning:true})");
  assert.equal(element('status').dataset.taskStatus, 'running'); assert.match(element('status').className, /status-running/);
  element('searchBtn').disabled = true; element('searchBtn').textContent = '任务运行中';
  run("syncTaskStatus({status:'completed',isRunning:false})");
  assert.match(element('status').innerHTML, /尚未开始任务/, 'a known background task completion clears only its own running indicator before the poll unlocks controls');
  element('searchBtn').disabled = false;
  route = { page: 'tasks', taskId: '', tab: '' };
  const collectionFixture = {taskId:'5',status:'partial',isRunning:false,result:{notes:[],collectionSummary:{filterCounts:{unknown_publish_time:7,duplicate:0},timeResolution:{attempted:9,resolved:2,unresolved:7,failed:1},timeReferenceAt:1792555200,zeroResultReason:'7 条发布时间待核验'}}};
  sandbox.collectionFixture=collectionFixture;
  run('renderTaskPanel(collectionFixture)');
  assert.match(element('stageCoverage').innerHTML,/7 条内容的发布时间待核验/);
  assert.doesNotMatch(element('stageCoverage').innerHTML,/额外补读详情|模型|质检|Prompt/);
  run("renderTaskPanel({taskId:'5',status:'completed',isRunning:false,result:{notes:[],collectionSummary:{rawSeen:122}}})");
  assert.match(element('stageCoverage').innerHTML,/未记录正文完整性/);
  assert.doesNotMatch(element('stageCoverage').innerHTML,/7 条|122/,'task switches must not retain another task’s counts');
  run("syncTaskStatus({status:'completed',isRunning:false})");
  assert.equal(element('status').dataset.taskStatus, 'completed');
  run("setStatus('用户当前操作结果', '✅')");
  assert.equal(element('status').dataset.taskStatus, '', 'foreground feedback is not mistaken for an old task status after navigation');
  responseFor = req => req.url === '/api/search/plan' ? { planId: 'memory-plan', plan: { ...req.body, keywords: ['智能体 委托', '智能体 外包'], planningMode: 'manual' } } : req.url === '/api/tasks/list' ? { tasks: [] } : {};
  workspace.step(1);
  assert.deepEqual(steps.map(step => step.queries.button.disabled), [false, true, true], 'unavailable wizard steps cannot receive a click');
  assert.equal(element('wbStep1Actions').hidden, false);
  workspace.step(3); assert.equal(requests.length, 0); assert.match(notices.at(-1), /生成搜索计划/);
  await run('search()');
  assert.deepEqual(steps.map(step => step.queries.button.disabled), [false, false, false]);
  assert.equal(element('wbStep1Actions').hidden, true, 'step-one actions must not remain visible in plan review');
  assert.equal(element('planPanel').style.display, 'block'); assert.equal(element('wbConfirmation').hidden, true);
  assert.equal(requests.filter(req => req.url === '/api/tasks/start').length, 0);
  element('planKeywords').value = '智能体 委托'; element('planGoal').value = '修改后的交付目标';
  workspace.step(3);
  assert.equal(element('planPanel').style.display, 'none'); assert.equal(element('wbConfirmation').hidden, false);
  assert.match(element('wbConfirmationSnapshot').innerHTML, /修改后的交付目标/); assert.match(element('wbConfirmationSnapshot').innerHTML, /智能体 委托/);
  assert.equal(requests.filter(req => req.url === '/api/tasks/start').length, 0, 'previewing final summary is still not consent to collect');
  workspace.step(2); assert.equal(element('wbConfirmation').hidden, true); assert.equal(element('planKeywords').value, '智能体 委托');

  const fixture = require('./workspace_mock_server.cjs').makeState().tasks[0].result;
  sandbox.fixture = fixture; run("currentTaskId='4'; cachedData=fixture; initializeLeadFilter(fixture); renderedTaskId='4'");
  const review1 = node('review-actions-0'), review2 = node('review-actions-1'), save1 = node('save-0','BUTTON'), save2 = node('save-1','BUTTON');
  review1.queries = { button: save1 }; review1.collections = { button: [save1, node('history-0','BUTTON')] };
  review2.queries = { button: save2 }; review2.collections = { button: [save2, node('history-1','BUTTON')] };
  const cards = [0, 1].map((index) => { const card = node(`card-${index}`); card.dataset.leadIndex = String(index); card.queries = { '.lead-actions': index ? review2 : review1, '.touch-actions': node(`touch-${index}`) }; card.collections = { '.lead-actions': [index ? review2 : review1, node(`feedback-${index}`)] }; return card; });
  const list = node('source-list'), heading = node('source-heading'); list.collections = { '.lead-card': cards }; list.queries = { '.wb-detail-empty': element('detail-empty') };
  sourcePanel = node('source-panel'); sourcePanel.queries = { '.lead-list': list, '.section-title': heading };
  workspace.resultsUpdated(fixture);
  assert.match(element('wbLeadRows').innerHTML, /data-index="0"/); assert.match(element('wbLeadRows').innerHTML, /data-index="1"/);
  assert.doesNotMatch(element('wbLeadRows').innerHTML, /data-index="[23]"/, 'default queue excludes supplier and already reviewed demand');
  assert.match(element('wbReviewCount-pending').innerHTML,/>2</);
  assert.match(element('wbReviewCount-approved').innerHTML,/>1</);
  workspace.filterReviews('approved');
  assert.match(element('wbLeadRows').innerHTML,/data-index="3"/);
  assert.doesNotMatch(element('wbLeadRows').innerHTML,/data-index="[012]"/);
  workspace.filterReviews('pending');
  element('wbLeadSearch').value='电商'; element('wbLeadSearch').fire('input');
  assert.match(element('wbReviewCount-pending').innerHTML,/>1</);
  assert.match(element('wbReviewCount-approved').innerHTML,/>0</);
  assert.match(element('wbReviewScope').textContent,/当前搜索结果/);
  element('wbLeadSearch').value=''; element('wbLeadSearch').fire('input');
  workspace.selectLead('lead-qualified-a');
  assert.equal(route.taskId, '4'); assert.equal(route.leadId, 'lead-qualified-a');
  responseFor = req => req.url === '/api/leads/review' ? { review: { status: req.body.status, note: req.body.note } } : {};
  element('reviewStatus-0').value = 'approved'; element('reviewNote-0').value = '已对照第一条原文';
  const beforeEdit = requests.filter(req => req.url === '/api/leads/review').length;
  element('reviewStatus-0').fire('change'); element('reviewNote-0').fire('input');
  assert.match(element('reviewResult-0').textContent, /尚未保存/);
  assert.equal(element('reviewResult-0').classList.contains('wb-unsaved'), true);
  assert.equal(requests.filter(req => req.url === '/api/leads/review').length, beforeEdit, 'select change and input mark a draft, never submit a review');
  const next = created.find(el => el.innerHTML === '保存并下一条'); assert.ok(next?.onclick);
  await next.onclick();
  assert.equal(element('reviewResult-0').classList.contains('wb-unsaved'), false, 'explicit successful save clears the draft indicator');
  assert.equal(requests.find(req => req.url === '/api/leads/review').body.leadId, 'lead-qualified-a');
  assert.equal(route.leadId, 'lead-uncertain-b', 'save and next selects the next original candidate ID');
  assert.doesNotMatch(element('wbLeadRows').innerHTML, /data-index="0"/);
  assert.match(element('wbExportCount').textContent, /2 条.*人工/);
  assert.match(element('wbReviewCount-pending').innerHTML,/>1</);
  assert.match(element('wbReviewCount-approved').innerHTML,/>2</);
  assert.match(element('wbExportCount').textContent,/独立于列表筛选/);

  const priorities = {taskId:4,confirmedPlan:{offeringId:'agent_custom_development'},leads:{leads:[
    {lead_id:'low',classification:'uncertain',qualified:false,review_priority:'low',inquiry_type:'insufficient_context',score:90,review:{status:'pending'}},
    {lead_id:'supplier',classification:'promotion',qualified:false,review_priority:'high',review:{status:'pending'}},
    {lead_id:'high',classification:'uncertain',review_priority:'high',inquiry_type:'price_inquiry',missing_information:['交付范围'],score:30,review:{status:'pending'}},
  ]}};
  sandbox.priorities=priorities; run("cachedData=priorities; leadViewFilter='uncertain'"); workspace.resultsUpdated(priorities);
  const priorityRows=element('wbLeadRows').innerHTML;
  assert.ok(priorityRows.indexOf('data-index="2"')<priorityRows.indexOf('data-index="0"'),'inquiries sort by recorded review priority while retaining raw lead indices');
  assert.doesNotMatch(priorityRows,/data-index="1"/); assert.match(priorityRows,/待确认询价.*优先核验/);
  assert.equal(priorities.leads.leads[2].score,30); assert.equal(priorities.leads.leads[2].qualified,undefined,'UI ranking must not qualify an inquiry');
  workspace.selectLead('high'); element('reviewStatus-2').value='rejected'; element('reviewNote-2').value='待补充委托范围';
  await run('saveReview(2)'); assert.equal(requests.filter(req=>req.url==='/api/leads/review').at(-1).body.leadId,'high');
  assert.match(element('wbReviewCount-pending').innerHTML,/>1</);
  assert.match(element('wbReviewCount-rejected').innerHTML,/>1</);
  workspace.filterReviews('rejected');
  assert.match(element('wbLeadRows').innerHTML,/data-index="2"/);
  assert.doesNotMatch(element('wbLeadRows').innerHTML,/data-index="[01]"/);
  workspace.filterReviews('pending');
  run("cachedData=fixture; leadViewFilter='pending'"); workspace.resultsUpdated(fixture);

  const draft = element('reviewNote-1'); draft.tagName = 'TEXTAREA'; draft.value = '尚未保存的核验说明'; draft.focus();
  const changed = { ...fixture, report: { summary: '后台新返回的报告' } };
  assert.equal(workspace.shouldDeferResults(changed), true, 'polling defers while an active review field is edited');
  assert.equal(draft.value, '尚未保存的核验说明'); active = null;
  assert.equal(workspace.shouldDeferResults(changed), false);

  // Polling reads serialize and only schedule a new request after the previous
  // response finishes. Failed sync keeps the last rendered task.
  sourcePanel = null; run("authenticated=true; currentTaskId='5'; currentTaskSnapshot={taskId:'5',isRunning:true}; cachedData=null");
  let resolveStatus, inFlight = 0, maxInFlight = 0;
  responseFor = req => req.url === '/api/tasks/status' ? new Promise(resolve => { inFlight++; maxInFlight = Math.max(maxInFlight, inFlight); resolveStatus = value => { inFlight--; resolve(value); }; }) : { tasks: [] };
  run("startPolling('5')"); await flush(); assert.equal(timers.size, 0);
  resolveStatus({ taskId: '5', isRunning: true, status: 'running', result: {} }); await flush();
  assert.equal(maxInFlight, 1); assert.equal([...timers.values()].at(-1).delay, 2000);
  run('stopTaskPolling()'); assert.equal(timers.size, 0);
  responseFor=req=>req.url==='/api/tasks/status'?{taskId:'5',status:'completed',isRunning:false,isEvolving:true,evolution:{status:'running'},result:{}}:{tasks:[]};
  run("startPolling('5')"); await flush(); assert.equal([...timers.values()].at(-1).delay,2000,'completed execution keeps polling its asynchronous evolution');
  assert.match(element('wbTaskInsights').innerHTML,/任务已完成/);
  assert.doesNotMatch(element('wbTaskInsights').innerHTML,/正在复盘|版本改进/,'background maintenance must not appear as unfinished business work');
  responseFor=req=>req.url==='/api/tasks/status'?{taskId:'5',status:'completed',isRunning:false,isEvolving:false,evolution:{status:'needs_review'},result:{}}:{tasks:[]};
  await run("pollStatus('5')"); assert.equal(timers.size,0,'polling stops only when both execution and evolution are idle'); await flush();
  responseFor = () => { throw new Error('fixture offline'); };
  await run("pollStatus('5')"); assert.equal(element('wbConnection').hidden, false); assert.match(element('wbConnectionText').textContent, /连接中断/);
  assert.equal(run('currentTaskSnapshot.taskId'), '5');
  workspace.connectionUpdated(true);
  let rejectOld;
  responseFor = () => new Promise((_resolve, reject) => { rejectOld = reject; });
  const staleFailure = run("pollStatus('5')"); await flush();
  run("currentTaskId='6'; currentTaskSnapshot={taskId:'6',isRunning:false}");
  rejectOld(new Error('old task failed after a task switch')); await staleFailure;
  assert.equal(element('wbConnection').hidden, true, 'old task errors must not put the newly selected task into a disconnected state');
  // A deferred result created while typing belongs to its original task. A
  // delayed focusout must never paint it into the newly selected task.
  run("currentTaskId='4'; cachedData=fixture; renderedTaskId='4'; currentTaskSnapshot={taskId:'4',isRunning:false}");
  draft.focus();
  const deferred = { ...fixture, report: { summary: '旧任务等待失焦的结果，不能显示在新任务' } };
  assert.equal(workspace.shouldDeferResults(deferred), true);
  run("currentTaskId='2'"); active = null; workspace.flushPendingResults();
  assert.notEqual(run('cachedData.report.summary'), deferred.report.summary, 'deferred results are task-bound even before navigation cleanup');
  run("currentTaskId='4'; cachedData=fixture; renderedTaskId='4'"); draft.focus();
  assert.equal(workspace.shouldDeferResults({ ...deferred, pendingRevision: 2 }), true);
  responseFor = () => { throw new Error('selected task unavailable'); };
  await run("viewTask('2')"); active = null; workspace.flushPendingResults();
  assert.equal(run('currentTaskId'), '2'); assert.equal(run('cachedData'), null); assert.equal(run('renderedTaskId'), null);
  assert.doesNotMatch(element('output').innerHTML, /data-lead-index|lead-qualified-a/);
  const reviewsBefore = requests.filter(req => req.url === '/api/leads/review').length;
  await run('saveReview(0)');
  assert.equal(requests.filter(req => req.url === '/api/leads/review').length, reviewsBefore, 'an old visible index cannot submit review under the failed new task');
  run("currentTaskId='4'; cachedData=fixture; cachedData.leads.leads[0].review={status:'pending'}");
  workspace.selectLead('lead-qualified-a');
  let resolveReview;
  responseFor = () => new Promise(resolve => { resolveReview = resolve; });
  const delayedReview = next.onclick(); await flush();
  sandbox.otherTask = { taskId: 2, leads: { leads: [{ lead_id: 'different-task-lead', review: { status: 'pending' } }] } };
  run("currentTaskId='2'; cachedData=otherTask");
  route = { page: 'reviews', taskId: '2', leadId: 'different-task-lead' };
  element('reviewResult-0').textContent = '新任务的待审核状态';
  resolveReview({ review: { status: 'approved', note: '旧任务的响应' } }); await delayedReview;
  assert.equal(route.leadId, 'different-task-lead', 'late save-and-next cannot select an old-task candidate in a new task');
  assert.equal(element('reviewResult-0').textContent, '新任务的待审核状态');
  assert.equal(run('cachedData.leads.leads[0].review.status'), 'pending');
  assert.equal(requests.filter(req => req.url === '/api/leads/review').at(-1).body.taskId, '4', 'the submitted review itself remains bound to the source task');
  run("currentTaskId='4'; cachedData=fixture");
  let resolveHistory;
  responseFor = () => new Promise(resolve => { resolveHistory = resolve; });
  const oldHistory = run('viewReviewHistory(0)'); await flush();
  const historyRequest = requests.at(-1);
  assert.match(historyRequest.url, /leadId=lead-qualified-a/); assert.match(historyRequest.url, /taskId=4/);
  run("currentTaskId='2'; cachedData=otherTask"); element('wbReviewHistoryBody').innerHTML = '新任务历史仍未打开';
  resolveHistory({ history: [{ status: 'approved', note: '旧任务记录不可打开' }] }); await oldHistory;
  assert.equal(element('wbReviewHistoryBody').innerHTML, '新任务历史仍未打开', 'late old-task history cannot open under the new context');
  run("currentTaskId='4'; cachedData=fixture");
  let rejectHistory; responseFor = () => new Promise((_resolve, reject) => { rejectHistory = reject; });
  const oldHistoryFailure = run('viewReviewHistory(0)'); await flush();
  run("currentTaskId='2'; cachedData=otherTask"); const noticesBeforeHistoryFailure = notices.length;
  rejectHistory(new Error('旧任务请求失败')); await oldHistoryFailure;
  assert.equal(notices.length, noticesBeforeHistoryFailure, 'late history errors do not disturb the newly selected task');
  sandbox.innerWidth = 390;
  const firstNavLink = node('first-nav-link', 'A'); element('wbSidebar').queries = { 'a.active, a': firstNavLink };
  element('menu-button').focus(); workspace.toggleNav();
  assert.equal(element('wbSidebar').classList.contains('is-open'), true);
  assert.equal(element('menu-button').getAttribute('aria-expanded'), 'true');
  assert.equal(element('wbNavBackdrop').hidden, false); assert.equal(element('main-frame').inert, true);
  assert.equal(active, firstNavLink, 'opening mobile navigation focuses a navigation link');
  workspace.toggleNav();
  assert.equal(element('wbSidebar').classList.contains('is-open'), false);
  assert.equal(element('menu-button').getAttribute('aria-expanded'), 'false');
  assert.equal(element('wbNavBackdrop').hidden, true); assert.equal(element('main-frame').inert, false);
  assert.equal(active, element('menu-button'), 'closing navigation restores the opener');
  sandbox.innerWidth = 1440; workspace.toggleNav();
  assert.equal(element('wbSidebar').classList.contains('is-open'), false, 'desktop sidebar is never a mobile overlay');
  console.log('PASS: three-step planning gates, unsaved review feedback, stable-ID save/next, mobile navigation focus/background, editing protection and completion-based polling.');
})().catch(error => { console.error(error); process.exitCode = 1; });
