/* Focused offering UI regression with memory-only APIs and no real browser/account. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assets = path.resolve(__dirname, '../assets');
const html = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { id, value: '', checked: false, disabled: false, dataset: {}, style: {}, textContent: '', innerHTML: '', previousElementSibling: { textContent: id }, classList: { add() {}, remove() {}, toggle() {} }, addEventListener() {}, focus() {}, scrollIntoView() {}, querySelectorAll() { return []; } });
  return elements.get(id);
}
for (const match of html.matchAll(/<(input|select|textarea)[^>]*id="([^"]+)"[^>]*>/g)) {
  const el = element(match[2]); el.value = match[0].match(/value="([^"]*)"/)?.[1] || ''; el.checked = /\schecked\b/.test(match[0]);
  if (match[1] === 'select') el.value = html.slice(match.index).match(/<option value="([^"]*)"/)?.[1] || '';
  if (match[1] === 'textarea') el.value = html.slice(match.index + match[0].length).split('</textarea>')[0];
}
const storage = () => { const map = new Map(); return { getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k) }; };
const requests = [], alerts = [];
let responseFor = () => ({});
const sandbox = { console, URL, Date, JSON, Object, Array, Number, String, Set,
  document: { getElementById: element, addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; } },
  location: { protocol: 'http:', origin: 'http://127.0.0.1:8765' }, localStorage: storage(), sessionStorage: storage(), navigator: {},
  window: { addEventListener() {}, open() {} },
  alert: message => alerts.push(message), prompt: () => '测试服务模板', confirm: () => true,
  setInterval: () => 1, clearInterval() {}, setTimeout: () => 1,
  fetch: async (url, options = {}) => {
    const request = { url, ...options, body: options.body ? JSON.parse(options.body) : null }; requests.push(request);
    const data = await responseFor(request);
    return { status: 200, ok: true, json: async () => ({ ok: true, data }) };
  }
};
const context = vm.createContext(sandbox), run = source => vm.runInContext(source, context);
require('./ui-libraries.cjs').loadUiLibraries(context, path.resolve(__dirname, '../assets'));
for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) run(script[1]);
run(fs.readFileSync(path.join(assets, 'workbench.js'), 'utf8'));
const plain = value => JSON.parse(JSON.stringify(value));
(async () => {
  const initial = plain(run('payload()'));
  assert.equal(initial.offeringId, 'agent_custom_development');
  assert.deepEqual(initial.seedKeywords, ['智能体']);
  assert.equal(initial.searchGoal, run('OFFERING_GOALS.agent_custom_development'));
  element('demandDescription').value = '只找有明确交付周期的委托';
  element('offeringId').value = 'general'; run('changeOffering()');
  assert.equal(element('demandDescription').value, '只找有明确交付周期的委托', 'mode changes preserve custom goals');
  element('offeringId').value = 'agent_custom_development'; run('changeOffering()');
  run("taskTemplates=[{name:'旧模板',plan:{seedKeywords:['历史关键词'],searchGoal:'历史通用目标'}},{name:'定制服务',plan:{offeringId:'agent_custom_development',seedKeywords:['智能体'],searchGoal:'寻找外包委托'}}]");
  element('taskTemplate').value = '0'; run('loadTaskTemplate()');
  assert.equal(run('payload().offeringId'), 'general');
  assert.equal(element('demandDescription').value, '历史通用目标');
  element('taskTemplate').value = '1'; run('loadTaskTemplate()');
  assert.equal(run('payload().offeringId'), 'agent_custom_development');
  assert.equal(element('demandDescription').value, '寻找外包委托');
  responseFor = req => req.url === '/api/templates' ? { templates: [] } : req.body?.plan || {};
  await run('saveTaskTemplate()'); await run('saveTaskDefaults()');
  for (const url of ['/api/templates', '/api/collection/defaults']) {
    assert.equal(requests.find(req => req.url === url && req.body?.plan).body.plan.offeringId, 'agent_custom_development');
  }
  run("workbenchConfig.collectionDefaults={offeringId:'general'}"); await run('restoreTaskDefaults()');
  assert.equal(run('payload().offeringId'), 'general');
  assert.equal(element('demandDescription').value, '寻找外包委托');
  element('offeringId').value = 'agent_custom_development'; run('changeOffering()');
  responseFor = req => req.url === '/api/search/plan'
    ? { planId: 'plan-focus', plan: { ...req.body, keywords: ['智能体 外包', '智能体 找人开发'], keywordReasons: {} } }
    : req.url === '/api/tasks/start' ? { taskId: 11, status: 'running', isRunning: true, confirmedPlan: req.body.confirmedPlan }
    : { tasks: [] };
  run('startPolling = () => {}; loadTaskHistory = async () => {};');
  await run('search()');
  assert.match(element('planOffering').textContent, /智能体定制开发/);
  assert.equal(element('planGoal').value, '寻找外包委托');
  element('planKeywords').value = '智能体 定制报价';
  element('planGoal').value = '确认的专属目标';
  await run('startConfirmedSearch()');
  const confirmed = requests.find(req => req.url === '/api/tasks/start').body.confirmedPlan;
  assert.equal(confirmed.offeringId, 'agent_custom_development');
  assert.deepEqual(confirmed.keywords, ['智能体 定制报价']);
  assert.equal(confirmed.searchGoal, '确认的专属目标');
  await run('search()'); element('offeringId').value = 'general'; run('changeOffering()');
  assert.equal(run('activeSearchPlan'), null, 'a changed mode invalidates the preview');
  element('offeringId').value = 'agent_custom_development'; run('changeOffering()');

  const baseLead = { platform: 'xhs', note_id: 'n1', note_title: '来源', note_url: 'https://www.xiaohongshu.com/explore/n1', comment_id: 'c1', target_user_id: 'buyer-one', review: { status: 'pending' } };
  sandbox.focused = {
    taskId: 11, confirmedPlan: confirmed, notes: [],
    leads: { leads: [
      { ...baseLead, lead_id: 'supplier', classification: 'promotion', qualified: false, intent_role: 'supplier', service_fit: 'custom_development', evidence: '我接定制开发' },
      { ...baseLead, lead_id: 'buyer', classification: 'demand', qualified: true, intent_role: 'buyer', service_fit: 'custom_development', demand_origin: 'self', intent_reason: '明确委托交付并询价', evidence: '找人开发智能体，请报价' },
      { ...baseLead, lead_id: 'needs-context', classification: 'demand', qualified: false, intent_role: 'unclear', evidence: '多少钱' },
      { ...baseLead, lead_id: 'uncertain', classification: 'uncertain', qualified: false, evidence: '有人会做吗' },
      { ...baseLead, lead_id: 'learner', classification: 'information', qualified: false, evidence: '求教程' },
      { ...baseLead, lead_id: 'invalid-boolean', classification: 'demand', qualified: 'true', evidence: '缺少可靠资格' }
    ] },
    stageCoverage: { search: { status: 'completed', completed: 2, total: 2, unit: 'query' }, relevance: { status: 'partial', completed: 1, total: 2, unit: 'note', error: '<script>timeout</script>' }, details: { status: 'pending', completed: 0, total: 1, unit: 'note' }, comments: { status: 'running', completed: 1, total: 2, unit: 'note' }, lead_signal: { status: 'completed', completed: 6, total: 6, unit: 'candidate' }, qa: { status: 'failed', completed: 0, total: 6, unit: 'candidate', error: '总时限超时' }, report: { status: 'skipped', completed: 0, total: 1, unit: 'report', error: '前序分析未完成' } }
  };
  run('render(focused)');
  assert.equal(run('leadViewFilter'), 'pending');
  assert.deepEqual(plain(run('filteredLeadEntries().map(item=>item.index)')), [1, 2, 3, 5], 'default demand category contains qualified and uncertain demand, with review status filtered independently');
  assert.match(element('output').innerHTML, /data-lead-index="1"/);
  assert.doesNotMatch(element('output').innerHTML, /data-lead-index="0"/);
  assert.match(element('output').innerHTML, /买方寻求服务/);
  assert.match(element('output').innerHTML, /本人需求/);
  const coverage = run('stageCoverageHtml(focused)');
  assert.match(coverage, /相关性研判：部分完成/);
  assert.match(coverage, /报告生成：未执行/);
  assert.match(coverage, /2 \/ 2 关键词×平台组合/);
  assert.match(coverage, /1 \/ 2 篇笔记/);
  assert.match(coverage, /&lt;script&gt;timeout/);
  assert.doesNotMatch(coverage, /<script>/);
  responseFor = req => req.url === '/api/leads/review' ? { review: { status: req.body.status, note: req.body.note } } : {};
  element('reviewStatus-1').value = 'approved'; element('reviewNote-1').value = '核对买方原文';
  await run('saveReview(1)');
  assert.equal(requests.find(req => req.url === '/api/leads/review').body.leadId, 'buyer');
  assert.deepEqual(plain(run('filteredLeadEntries().filter(({lead})=>(lead.review?.status||"pending")==="pending").map(item=>item.index)')), [2, 3, 5], 'approved demand leaves the pending review queue without reindexing remaining records');
  run("changeLeadFilter('qualified')");
  assert.deepEqual(plain(run('filteredLeadEntries().map(item=>item.index)')), [1], 'explicit qualified view retains reviewed items for later reference');
  run("openOutreach(1,'send_dm')");
  assert.equal(run('outreachContext.lead.lead_id'), 'buyer');
  run("changeLeadFilter('uncertain')");
  assert.deepEqual(plain(run('filteredLeadEntries().map(item=>item.index)')), [2, 3, 5]);
  run("changeLeadFilter('all')");
  assert.deepEqual(plain(run('filteredLeadEntries().map(item=>item.index)')), [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(plain(run('cachedData.leads.leads.map(lead=>lead.lead_id)')), sandbox.focused.leads.leads.map(lead=>lead.lead_id));
  assert.equal(run('selectedExportLeads()[0].lead_id'), 'buyer');
  for(const status of ['needs_review','pending','rejected']) {
    run(`focused.leads.leads[1].qa_status='${status}'; changeLeadFilter('qualified')`);
    assert.deepEqual(plain(run('filteredLeadEntries().map(item=>item.index)')),[],'known non-approved QA cannot display as qualified even for old qualified:true data');
    run("changeLeadFilter('uncertain')"); assert.ok(plain(run('filteredLeadEntries().map(item=>item.index)')).includes(1));
    assert.equal(run('qualificationDisplayLabel(focused.leads.leads[1])'),'研判初步符合，质检待核验');
    assert.equal(run('focused.leads.leads[1].qualified'),true,'display guard must not rewrite stored task qualification');
    assert.equal(run('focused.leads.leads[1].review.status'),'approved','QA display cannot overwrite a saved human decision');
  }
  run("focused.leads.leads[1].qa_status='approved'; delete focused.leads.leads[1].review; changeLeadFilter('qualified')");
  assert.deepEqual(plain(run('filteredLeadEntries().map(item=>item.index)')),[1],'missing human review does not change qualification');
  run("focused.leads.leads[1].review={status:'approved',note:'核对买方原文'}; delete focused.leads.leads[1].qa_status");
  assert.match(run('qualificationDisplayLabel(focused.leads.leads[1])'),/质检未记录/);

  run("changeLeadFilter('qualified'); focused.leads.leads=focused.leads.leads.filter(lead=>lead.qualified!==true); render(focused)");
  assert.match(element('output').innerHTML, /没有发现已满足条件/);
  assert.doesNotMatch(element('output').innerHTML, /data-lead-index=/);

  sandbox.legacy = { taskId: 3, status: 'partial', notes: [{ title: '旧笔记' }], qa: {}, report: {}, leads: { leads: [{ lead_id: 'old-discussion', classification: 'information' }] } };
  run("currentTaskId='3'; currentTaskSnapshot={taskId:3,status:'partial',result:legacy}; render(legacy)");
  assert.equal(run('resultOfferingId(legacy)'), 'general');
  assert.equal(run('leadViewFilter'), 'all');
  assert.equal(run('getDemandDescription(legacy)'), '');
  assert.match(element('output').innerHTML, /本任务服务：<\/b>通用关键词研究/);
  assert.match(element('output').innerHTML, /data-lead-index="0"/);
  const legacyCoverage = run('stageCoverageHtml(legacy)');
  assert.match(legacyCoverage, /未见已保存的报告，未确认完成/);
  assert.match(legacyCoverage, /未见已保存的质检产物，未确认完成/);
  assert.doesNotMatch(legacyCoverage, /：已完成/);
  // Both delivered formats must keep general research separate from a custom
  // service qualification, even if an old result contains qualified=true.
  const exports = [];
  sandbox.OfflineExcel = { download: sheets => exports.push({ sheets: plain(sheets) }) };
  sandbox.captureMarkdown = text => exports.push({ markdown: text });
  run('downloadText = text => captureMarkdown(text)');
  run("legacy.leads.leads[0].qualified=true; legacy.leads.leads[0].review={status:'approved'}; legacy.leads.leads[0].qa_status='rejected'; exportExcel(); exportMarkdown()");
  assert.equal(exports[0].sheets[0].rows[0]['定制需求资格'], '不适用（通用关键词研究）');
  assert.equal(exports[0].sheets[0].rows[0]['质检状态'], '质检未通过');
  assert.match(exports[1].markdown, /定制需求资格：不适用（通用关键词研究）/);
  assert.match(exports[1].markdown, /质检状态：质检未通过/);
  assert.doesNotMatch(exports[1].markdown, /合格定制需求/);
  run("legacy.offeringId='agent_custom_development'; exportExcel(); exportMarkdown(); delete legacy.offeringId");
  assert.equal(exports[2].sheets[0].rows[0]['定制需求资格'], '研判初步符合，质检待核验');
  assert.match(exports[3].markdown, /质检状态：质检未通过/);
  const contactUrl = 'https://example.test/13812345678?note_id=source-1&xsec_token=fixture-token';
  sandbox.contactFixture = {taskId:18,status:'completed',notes:[{id:'source-1',title:'联系示例作者',author:'示例作者',content:'联系13812345678或a@example.com',url:contactUrl}],
    leads:{leads:[{lead_id:'contact-1',nickname:'示例昵称',user_id:'12345678901234567890',phone:'13812345678',email:'a@example.com',classification:'demand',qa_status:'approved',review:{status:'approved'},
      evidence:'需要开发服务',content:'需要开发服务，请联系13812345678或a@example.com',note_url:contactUrl,outreach_copy:'可联系13812345678，a@example.com；api_key=fixture-key'}]}};
  const contactSnapshot=JSON.stringify([sandbox.contactFixture.notes,sandbox.contactFixture.leads]);
  run("currentTaskId='18'; render(contactFixture); exportScope='all'; exportExcel(); exportMarkdown()");
  assert.match(element('output').innerHTML,/示例昵称.*12345678901234567890/s);
  assert.match(element('output').innerHTML,/完整来源内容.*13812345678或a@example.com/s);
  assert.doesNotMatch(element('output').innerHTML,/fixture-token|fixture-key/);
  const contactSheets=exports.at(-2).sheets, contactMarkdown=exports.at(-1).markdown;
  assert.equal(contactSheets[0].rows[0]['用户昵称'],'示例昵称'); assert.equal(contactSheets[0].rows[0]['用户编号'],'12345678901234567890');
  assert.equal(contactSheets[0].rows[0]['电话'],'13812345678'); assert.equal(contactSheets[0].rows[0]['邮箱'],'a@example.com');
  assert.match(contactSheets[0].rows[0]['原始内容'],/13812345678或a@example.com/);
  assert.equal(contactSheets[1].rows[0]['作者'],'示例作者'); assert.match(contactSheets[1].rows[0]['正文'],/13812345678或a@example.com/);
  assert.equal(contactSheets[0].rows[0]['来源链接'],'https://example.test/13812345678?note_id=source-1');
  assert.match(contactMarkdown,/用户昵称：示例昵称/); assert.match(contactMarkdown,/12345678901234567890/); assert.match(contactMarkdown,/13812345678或a@example.com/);
  assert.doesNotMatch(JSON.stringify(contactSheets) + contactMarkdown,/fixture-token|fixture-key|用户已脱敏|手机已脱敏|邮箱已脱敏/);
  assert.equal(JSON.stringify([sandbox.contactFixture.notes,sandbox.contactFixture.leads]),contactSnapshot,'display/export must preserve the source record');
  const generatedReport = {summary:'关键词 Agent 与 reason：qualifiedCount=0；commentsCollected=0；visited=true。',insights:['noteContexts=[]；classificationCounts={}。'],recommended_actions:['请核对 endReasons 与 no_more_results。'],risks:['outside_time_range=1；failures=[]。']};
  sandbox.reportFixture={taskId:3,confirmedPlan:{keywords:['reason'],seedKeywords:['Agent']},notes:[],leads:{leads:[]},report:generatedReport}; const reportSnapshot=JSON.stringify(generatedReport);
  run('render(reportFixture)');
  for (const text of [generatedReport.summary,...generatedReport.insights,...generatedReport.recommended_actions,...generatedReport.risks]) {
    sandbox.generatedProse=text; const displayed=run('esc(reportText(generatedProse, reportFixture))');
    assert.ok(element('output').innerHTML.includes(displayed),'every generated report section must use the shared Chinese renderer');
    assert.notEqual(displayed,text,'fixture technical prose must be translated');
  }
  assert.doesNotMatch(element('output').innerHTML,/<summary>技术详情<\/summary>/); assert.equal(JSON.stringify(generatedReport),reportSnapshot,'rendering must preserve the original report record');
  assert.match(run('reportText(reportFixture.report.summary, reportFixture)'),/关键词 Agent 与 reason/,'known field names used as task keywords remain original');
  run("cachedData={taskId:999,confirmedPlan:{keywords:['qualifiedCount']}};");
  assert.match(run('reportText(reportFixture.report.summary, reportFixture)'),/关键词 Agent 与 reason.*为 0 条/,'explicit render task data takes precedence over another cached task');
  run('cachedData=reportFixture');
  run("exportScope='all'; exportMarkdown()");
  assert.doesNotMatch(exports.at(-1).markdown,/qualifiedCount|commentsCollected|visited=true|outside_time_range|failures=\[\]/,'shared Markdown exports use readable generated summaries and risks');
  assert.match(exports.at(-1).markdown,/关键词 Agent 与 reason/,'export preserves the same task-specific keyword names');
  const harness = fs.readFileSync(path.join(assets, 'harness.html'), 'utf8');
  const harnessContext = vm.createContext({ ...sandbox, legacy: sandbox.legacy, focused: sandbox.focused });
  for (const script of harness.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInContext(script[1], harnessContext);
  const harnessRun = source => vm.runInContext(source, harnessContext);
  harnessRun("$('harnessStageCoverage').innerHTML = renderHarnessStageCoverage({taskId:3,status:'partial',result:legacy})");
  assert.match(element('harnessStageCoverage').innerHTML, /通用关键词研究/);
  assert.match(element('harnessStageCoverage').innerHTML, /未见已保存的报告，未确认完成/);
  harnessRun("$('harnessStageCoverage').innerHTML = renderHarnessStageCoverage({taskId:11,status:'partial',result:focused})");
  assert.match(element('harnessStageCoverage').innerHTML, /智能体定制开发/);
  assert.match(element('harnessStageCoverage').innerHTML, /质量核验：失败/);
  assert.match(element('harnessStageCoverage').innerHTML, /2 \/ 2 关键词×平台组合/);
  sandbox.focused.stageCoverageScope = 'executed_rounds';
  sandbox.focused.roundStageCoverage = [{ round: 1 }, { round: 2 }];
  assert.match(run('stageCoverageHtml(focused)'), /按已执行轮次累计/);
  assert.match(run('stageCoverageHtml(focused)'), /当前记录 2 轮/);
  harnessRun("$('harnessStageCoverage').innerHTML = renderHarnessStageCoverage({taskId:11,status:'partial',result:focused})");
  assert.match(element('harnessStageCoverage').innerHTML, /不能与单轮计划数量直接比较/);

  // Every real entry route renders a warning for partial tasks; a stale poll cannot
  // replace the newly selected task's banner or results.
  run("renderTaskPanel({taskId:3,status:'completed',isRunning:false,result:legacy})");
  assert.match(element('status').className, /status-success/);
  run("renderTaskPanel({taskId:3,status:'partial',statusText:'模型阶段超时',isRunning:false,result:legacy})");
  assert.match(element('status').className, /status-warning/);
  assert.match(element('status').innerHTML, /部分完成/);
  responseFor = req => req.url === '/api/tasks/status' ? { taskId: req.body.taskId, status: 'partial', statusText: '仍有未完成阶段', isRunning: false, result: sandbox.legacy } : { tasks: [] };
  await run("viewTask('3')");
  assert.match(element('status').className, /status-warning/);
  assert.match(element('status').innerHTML, /部分完成/);
  await run('restoreLastTask()');
  assert.match(element('status').innerHTML, /已恢复任务 #3：部分完成/);
  let resolveStale;
  responseFor = req => new Promise(resolve => { resolveStale = resolve; });
  run("currentTaskId='21'");
  const stalePoll = run("pollStatus('21')");
  run("currentTaskId='22'; renderTaskPanel({taskId:22,status:'partial',isRunning:false,result:legacy})");
  resolveStale({ taskId: 21, status: 'completed', isRunning: false, result: {} });
  await stalePoll;
  assert.match(element('taskIdBadge').textContent, /#22 · 部分完成/);
  assert.match(element('status').className, /status-warning/);
  assert.equal(alerts.length, 0, alerts.join('\n'));
  console.log('PASS: focused defaults, preserved plans, original-index filtering, conservative legacy/zero results, batch coverage scope, partial task banners across view/restore/poll and stale-response protection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
