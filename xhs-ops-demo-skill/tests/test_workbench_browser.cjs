/* Optional real browser UI smoke with fully mocked APIs, no platform operations. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const assets = path.resolve(__dirname, '../assets');
const server = http.createServer((req, res) => {
  const requested = new URL(req.url, 'http://localhost').pathname;
  const file = path.join(assets, requested === '/' ? 'index.html' : requested);
  if (!file.startsWith(assets + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end('Not found'); }
  res.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/html; charset=utf-8' }); res.end(fs.readFileSync(file));
});
const fixture = { searchGoal: '检索数据备份需求', notes: [{ id: 'note1', rank: 1, title: '业务数据备份咨询', author: '公开用户', url: 'https://www.xiaohongshu.com/explore/note1', comments: 2 }], leads: { totalCommentsScanned: 2, leads: [{ lead_id: 'lead1', platform: 'xhs', note_id: 'note1', comment_id: 'comment1', target_user_id: 'target-user', nickname: '测试对象', evidence: '请问异地备份怎么做？', demand_summary: '咨询异地备份', classification: 'demand', reason: '明确询问实现方式', note_title: '业务数据备份咨询', note_url: 'https://www.xiaohongshu.com/explore/note1', score: 82, priority: 'B', review: { status: 'pending' }, qa_status: 'approved' }] }, report: { summary: '本次检索识别出一条待人工复核的需求。' }, collectionSummary: { notesCollected: 1, commentsCollected: 2, requested: { maxNotes: 155, maxCommentsTotal: 800 }, endReasons: ['configured_pages_completed'] } };
let browser;
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch({ headless: true, chromiumSandbox: true, ...(process.env.WORKBENCH_CHROME ? { executablePath: process.env.WORKBENCH_CHROME } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const origin = `http://127.0.0.1:${server.address().port}`;
  // Only this test's static asset server is reachable; all APIs below are mocked.
  await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  let loggedIn = false, startCount = 0, executeCount = 0, statusCount = 0, settingsCount = 0, lastPlan;
  const reviewedLeadIds = [];
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  const authorScript = fs.readFileSync(path.resolve(__dirname, '../scripts/xhs_browser_actions.py'), 'utf8').match(/_COMMENT_AUTHOR_LINKS = """([\s\S]*?)"""/)[1];
  const authorFunction = require('node:vm').runInNewContext(`(${authorScript})`);
  await page.setContent(`<article id="author-fixture" data-comment-id="parent">
    <div class="author-wrapper"><a href="https://www.xiaohongshu.com/user/profile/actual-author">作者</a></div>
    <div class="content"><a class="author" href="https://www.xiaohongshu.com/user/profile/account-a">@当前账号</a></div>
    <article data-comment-id="nested"><div class="author-wrapper"><a href="https://www.xiaohongshu.com/user/profile/account-a">子回复作者</a></div></article>
  </article>`);
  assert.deepEqual(await page.locator('#author-fixture').evaluate(authorFunction), ['https://www.xiaohongshu.com/user/profile/actual-author']);
  await page.locator('#author-fixture > .author-wrapper').evaluate(element => element.remove());
  assert.deepEqual(await page.locator('#author-fixture').evaluate(authorFunction), [], 'mentions/nested replies cannot provide the parent author');
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), body = req.postDataJSON() || {}; let data = {};
    if (url.pathname === '/api/auth/me' && !loggedIn) return route.fulfill({ status: 401, json: { ok: false, error: 'login required' } });
    switch (url.pathname) {
      case '/api/auth/login': loggedIn = true; data = { user: { username: 'admin', role: 'admin' } }; break;
      case '/api/auth/me': data = { user: { username: 'admin', role: 'admin' } }; break;
      case '/api/setup/status': data = { hasAdmin: true }; break;
      case '/api/config': data = { platforms: { enabled: ['xhs'] } }; break;
      case '/api/settings': settingsCount++; data = { llm: { connect_timeout_s: 30, read_timeout_s: 60, total_timeout_s: 300 }, platforms: { enabled: ['xhs'] } }; break;
      case '/api/templates': data = { templates: [] }; break;
      case '/api/tasks/list': case '/api/admin/tasks/list': data = { tasks: [] }; break;
      case '/api/outreach/list': data = { actions: [] }; break;
      case '/api/search/plan': lastPlan = body; data = { planId: 'plan1', plan: { ...body, keywords: ['数据备份', 'AI新增关键词'], keywordReasons: { 数据备份: '原始种子词', AI新增关键词: '扩展讨论场景' }, planningMode: 'llm' } }; break;
      case '/api/tasks/start': startCount++; assert.deepEqual(body.confirmedPlan.keywords, ['workbuddy', '数据备份']); assert.deepEqual(body.confirmedPlan.platforms, ['xhs']); assert.equal(body.mode,'single'); assert.equal(body.confirmedPlan.offeringId,'general'); assert.equal(body.confirmedPlan.maxNotes, 155); data = { taskId: 1, isRunning: true }; break;
      case '/api/tasks/status': statusCount++; data = { taskId: 1, status: 'completed', isRunning: false, progress: { percent: 100 }, result: fixture }; break;
      case '/api/leads/review': reviewedLeadIds.push(body.leadId); fixture.leads.leads[0].review = { status: body.status, note: body.note }; data = { review: fixture.leads.leads[0].review }; break;
      case '/api/outreach/prepare': assert.equal(body.targetUrl, 'https://www.xiaohongshu.com/user/profile/target-user'); data = { ...body, actionId: 'action1', confirmationToken: 'confirm1', accountIdentity: 'xhs:test-account', status: 'prepared' }; break;
      case '/api/outreach/execute': executeCount++; data = { actionId: 'action1', status: 'unknown', error: '模拟提交后回执超时' }; break;
      case '/api/agents': data = { agents: [] }; break;
      case '/api/prompts': data = { prompts: [] }; break;
      case '/api/feedback/list': data = { items: [] }; break;
      case '/api/evals/datasets': data = { datasets: [] }; break;
      case '/api/evals/results': data = { runs: [] }; break;
    }
    await route.fulfill({ status: 200, json: { ok: true, data } });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.locator('#workbenchGate').waitFor({ state: 'visible' });
  await page.fill('#workbenchPassword', 'mock-test-password'); await page.click('#workbenchLoginBtn');
  await page.locator('#salesMain').waitFor({ state: 'visible' });
  await page.evaluate(() => {
    document.getElementById('searchBtn').classList.add('is-pressing');
    document.dispatchEvent(new Event('pointerdown'));
    document.dispatchEvent(new Event('pointerleave'));
  });
  assert.equal(await page.locator('button.is-pressing').count(), 0);
  await page.waitForTimeout(10000); assert.equal(statusCount, 0, '新登录无任务时不应轮询任务状态'); assert.deepEqual(errors, []);
  await page.getByRole('button', { name: '＋ 新建任务', exact: true }).first().click();
  assert.equal(await page.locator('#offeringId').inputValue(), 'agent_custom_development');
  assert.equal(await page.locator('#keyword').inputValue(), '智能体');
  assert.match(await page.locator('#demandDescription').inputValue(), /外包交付或报价/);
  await page.selectOption('#offeringId', 'general');
  await page.fill('#keyword', 'workbuddy，数据备份');
  await page.locator('.wb-advanced > summary').click();
  await page.locator('#count').waitFor({state:'visible'});
  await page.fill('#count', '155'); await page.fill('#maxCommentsTotal', '800');
  await page.click('#searchBtn'); await page.locator('#planPanel').waitFor({ state: 'visible' });
  assert.equal(startCount, 0); assert.equal(lastPlan.maxNotes, 155); assert.deepEqual(lastPlan.seedKeywords,['workbuddy','数据备份']); assert.deepEqual(lastPlan.platforms,['xhs']);
  await page.fill('#planKeywords', 'workbuddy\n数据备份'); await page.click('#wbReviewPlanBtn');
  await page.locator('#wbConfirmation').waitFor({ state: 'visible' }); assert.equal(startCount, 0);
  await page.click('#confirmSearchBtn');
  await page.locator('[data-wb-lead="lead1"]').click();
  await page.locator('#reviewStatus-0').waitFor(); assert.equal(startCount, 1);
  await page.selectOption('#reviewStatus-0', 'approved'); await page.fill('#reviewNote-0', '已核对原文'); await page.getByRole('button', { name: '保存审核', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('reviewResult-0').textContent.includes('已保存'));
  assert.equal(await page.locator('#exportScope').inputValue(), 'approved');
  assert.match(await page.locator('#wbReviewCount-pending').innerText(),/^0/);
  assert.match(await page.locator('#wbReviewCount-approved').innerText(),/^1/);
  assert.equal(await page.locator('#wbStartReview').isDisabled(),true);
  assert.equal(await page.locator('[data-result-tab="logs"]').count(),0);
  assert.match(await page.locator('#wbHarnessLink').innerText(),/维护后台/);

  await page.selectOption('#wbReviewFilter', 'all'); await page.locator('[data-wb-lead="lead1"]').click();
  await page.locator('.wb-active-lead .wb-touch-disclosure summary').click();
  await page.getByRole('button', { name: '私信 / 草稿', exact: true }).click(); await page.fill('#dmDraftText', '你好，想了解你提到的数据备份需求。');
  await page.click('#prepareOutreachBtn'); await page.locator('#executeOutreachBtn').waitFor({ state: 'visible' });
  assert.match(await page.locator('#outreachConfirmation').textContent(), /xhs:test-account/);
  await page.click('#executeOutreachBtn'); await page.waitForFunction(() => document.getElementById('dmDraftStatus').textContent.includes('待核实'));
  assert.equal(executeCount, 1); assert.equal(await page.locator('#executeOutreachBtn').isVisible(), false);
  await page.evaluate(()=>renderOutreachResult({actionId:'action1',status:'unknown',error:'TimeoutError: waiting for locator "receipt"'}));
  assert.match(await page.locator('#dmDraftStatus').innerText(),/等待平台页面响应超时/);
  assert.match(await page.locator('#dmDraftStatus').innerText(),/结果待核实，禁止自动重发/,'error translation must retain the separate outreach outcome and retry boundary');
  assert.doesNotMatch(await page.locator('#dmDraftStatus').innerText(),/TimeoutError|waiting for locator/);
  await page.locator('#dmDraftStatus details > summary').click();
  assert.match(await page.locator('#dmDraftStatus').innerText(),/TimeoutError: waiting for locator/,'original platform error remains inspectable without changing action state');
  assert.equal(executeCount,1,'viewing technical details must never repeat outreach');
  await page.evaluate(() => closeDmDraft());
  const focusedResult = {
    taskId: 2, status: 'partial', confirmedPlan: { offeringId: 'agent_custom_development', searchGoal: '寻找有明确交付需求的智能体定制委托', seedKeywords: ['智能体'] }, notes: [], report: {}, qa: {},
    leads: { leads: [
      { ...fixture.leads.leads[0], lead_id: 'supplier', classification: 'promotion', qualified: false, evidence: '专业智能体定制服务，欢迎咨询', demand_summary: '服务商宣传' },
      { ...fixture.leads.leads[0], lead_id: 'qualified-buyer', target_user_id: 'buyer-new', classification: 'demand', qualified: true, intent_role: 'buyer', service_fit: 'custom_development', demand_origin: 'self', intent_reason: '明确寻求承接者并要求报价', evidence: '找人开发客服智能体，能交付的请报价', demand_summary: '客服智能体定制委托', review: { status: 'pending' } },
      { ...fixture.leads.leads[0], lead_id: 'uncertain-buyer', classification: 'uncertain', qualified: false, review_priority:'high', inquiry_type:'price_inquiry', missing_information:['交付范围','本人委托意图'], business_review_required:true, demand_origin: 'third_party', evidence: '听说有人想做一个智能体', demand_summary: '转述待确认' }
    ] },
    stageCoverage: { search: { status: 'completed', completed: 2, total: 2, unit: 'query' }, relevance: { status: 'completed', completed: 3, total: 3, unit: 'note' }, details: { status: 'completed', completed: 3, total: 3, unit: 'note' }, comments: { status: 'partial', completed: 2, total: 3, unit: 'note', error: '一篇评论读取失败' }, lead_signal: { status: 'completed', completed: 3, total: 3, unit: 'candidate' }, qa: { status: 'failed', completed: 0, total: 3, unit: 'candidate', error: '模型总时限超时' }, report: { status: 'skipped', completed: 0, total: 1, unit: 'report', error: '前序阶段未完成' } }
  };
  await page.evaluate(result => { currentTaskId = '2'; renderTaskPanel({ taskId: 2, status: 'partial', isRunning: false, result }); render(result); }, focusedResult);
  assert.match(await page.locator('#status').getAttribute('class'), /status-warning/);
  assert.match(await page.locator('#status').textContent(), /部分完成/);
  assert.equal(await page.locator('#leadFilter').inputValue(), 'pending');
  assert.equal(await page.locator('.wb-lead-row').count(), 2);
  assert.equal(await page.locator('.wb-lead-row').first().getAttribute('data-index'), '1');
  assert.match(await page.locator('#wbReviewCount-pending').innerText(),/^1/);
  assert.match(await page.locator('#wbReviewCount-approved').innerText(),/^1/);
  await page.click('#wbReviewCount-pending');
  assert.equal(await page.locator('.wb-lead-row').count(),1);
  await page.click('#wbStartReview');
  assert.equal(await page.locator('.wb-active-lead').getAttribute('data-lead-id'),'qualified-buyer');
  await page.fill('#wbLeadSearch','找不到的需求');
  assert.match(await page.locator('#wbReviewCount-pending').innerText(),/^0/);
  assert.equal(await page.locator('#wbStartReview').isDisabled(),true);
  await page.fill('#wbLeadSearch','');
  await page.selectOption('#wbReviewFilter','all');
  await page.evaluate(()=>switchResultTab('logs'));
  assert.equal(await page.locator('[data-result-panel="leads"]').isVisible(),true,'old logs links fall back to the lead queue');
  await page.screenshot({path:'/tmp/workbench-business-review-desktop.png',fullPage:true});
  await page.evaluate(()=>WorkbenchWorkspace.closeDetail());
  for (const width of [1024,760]) {
    await page.setViewportSize({width,height:1000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth),true,'business summary must fit smaller viewports');
  }
  await page.screenshot({path:'/tmp/workbench-business-review-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1100});

  assert.match(await page.locator('#stageCoverage').textContent(), /读取评论：1 条尚未完成/);
  assert.match(await page.locator('#stageCoverage').textContent(), /核对结果：3 条尚未完成/);
  await page.locator('[data-wb-lead="qualified-buyer"]').click();
  await page.selectOption('#reviewStatus-1', 'approved'); await page.fill('#reviewNote-1', '已核对委托原文');
  await page.getByRole('button', { name: '保存审核', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('reviewResult-1').textContent.includes('已保存'));
  assert.equal(reviewedLeadIds.at(-1), 'qualified-buyer');
  await page.selectOption('#wbReviewFilter', 'all'); await page.locator('[data-wb-lead="qualified-buyer"]').click();
  await page.locator('.wb-active-lead .wb-touch-disclosure summary').click();
  await page.getByRole('button', { name: '私信 / 草稿', exact: true }).click();
  assert.match(await page.locator('#dmDraftMeta').textContent(), /buyer-new/);
  await page.evaluate(() => closeDmDraft());
  await page.selectOption('#leadFilter', 'uncertain');
  assert.equal(await page.locator('.wb-lead-row').count(), 1);
  assert.equal(await page.locator('.wb-lead-row').first().getAttribute('data-index'), '2');
  assert.match(await page.locator('.wb-lead-row').textContent(), /待确认询价.*优先核验/);
  await page.locator('[data-wb-lead="uncertain-buyer"]').click();
  assert.match(await page.locator('.wb-active-lead .task-inquiry').textContent(),/交付范围.*本人委托意图/);
  assert.match(await page.locator('#wbTaskInsights').textContent(),/部分结果可用/);
  assert.doesNotMatch(await page.locator('#wbTaskInsights').textContent(),/模型质检|版本改进|待评测/);
  await page.screenshot({path:'/tmp/workbench-ui-inquiry-smoke.png',fullPage:true});
  await page.selectOption('#leadFilter', 'all'); assert.equal(await page.locator('.wb-lead-row').count(), 3);
  await page.selectOption('#leadFilter', 'qualified');
  await page.screenshot({ path: '/tmp/workbench-ui-focused-smoke.png', fullPage: true });
  await page.evaluate(result => { result.leads.leads = result.leads.leads.filter(lead => lead.qualified !== true); render(result); }, focusedResult);
  assert.equal(await page.locator('.wb-lead-row').count(), 0);
  assert.match(await page.locator('#output').textContent(), /没有发现已满足条件/);
  await page.screenshot({ path: process.env.WORKBENCH_SCREENSHOT || '/tmp/workbench-ui-browser-smoke.png', fullPage: true });
  const collectionResult = {taskId:5,status:'partial',isRunning:false,notes:[],progress:{stage:'search',percent:12},events:[{level:'info',agent:'Model Usage',message:'旧用量提醒'},{level:'info',agent:'模型用量',message:'旧中文用量提醒'},{level:'info',agent:'Model Request',message:'已开始请求模型 Qwen3.5，已接收 123 字符。'}],collectionSummary:{notesCollected:0,endReasons:['xhs:workbuddy:no_more_results'],
    filterCounts:{invalid_record:1,non_note:2,missing_note_id:3,duplicate:4,excluded_keyword:5,outside_time_range:6,unknown_publish_time:7,capacity_limit:0},
    timeResolution:{attempted:9,resolved:2,unresolved:7,failed:1},timeReferenceAt:1792555200,zeroResultReason:'7 条发布时间待核验，不能据此判断没有需求。'}};
  await page.evaluate(result => {currentTaskId='5'; renderTaskPanel(result); render(result);},collectionResult);
  assert.match(await page.locator('#taskProgressText').textContent(),/流程已结束/);
  assert.doesNotMatch(await page.locator('#taskProgressText').textContent(),/进度：/);
  assert.doesNotMatch(await page.locator('#taskProgressText').textContent(),/no_more_results|阶段：search|xhs:/);
  assert.deepEqual(await page.evaluate(()=>({stage:currentTaskSnapshot.progress.stage,reasons:currentTaskSnapshot.collectionSummary.endReasons})),{stage:'search',reasons:['xhs:workbuddy:no_more_results']},'Chinese display must not rewrite returned API fields');
  assert.equal(await page.locator('#taskEvents').isVisible(),false);
  assert.doesNotMatch(await page.locator('#taskEvents').textContent(),/Model Usage|模型用量|用量提醒/);
  if (!await page.locator('.wb-progress-disclosure').evaluate(element=>element.open)) await page.locator('.wb-progress-disclosure > summary').click();
  await page.locator('#stageCoverage .wb-task-explanation').waitFor({state:'visible'});
  assert.match(await page.locator('#stageCoverage').textContent(),/7 条内容的发布时间待核验/);
  assert.doesNotMatch(await page.locator('#stageCoverage').textContent(),/额外补读|模型质检|版本改进/);
  assert.match(await page.locator('#stageCoverage').textContent(),/不能仅凭任务结束或零结果认定没有需求/);
  await page.screenshot({path:'/tmp/workbench-ui-collection-smoke.png',fullPage:true});
  await page.evaluate(()=>WorkbenchWorkspace.taskUpdated(currentTaskSnapshot));
  assert.equal(await page.locator('#stageCoverage .wb-task-explanation').count(),1,'review-only updates must replace diagnostic metadata rather than duplicate it');
  await page.evaluate(result=>renderTaskPanel({...result,taskId:6,status:'running',isRunning:true}),collectionResult);
  assert.equal(await page.locator('#taskProgressText').isVisible(),true,'running task progress must be visible in the current layout');
  assert.match(await page.locator('#taskProgressText').innerText(),/搜索内容.*12%/);
  assert.doesNotMatch(await page.locator('#taskProgressText').innerText(),/no_more_results|阶段：search|xhs:/);
  await page.screenshot({path:'/tmp/workbench-ui-running-chinese-smoke.png',fullPage:true});
  const readableReport = {taskId:6,status:'completed',isRunning:false,notes:[],leads:{leads:[]},confirmedPlan:{offeringId:'general',keywords:['workbuddy']},
    report:{summary:'本次覆盖 xhs 6 个关键词；topLeads 与 noteContexts 为空，qualifiedCount=0、leadsCount=0。不能判定无需求。',
      insights:['全部 visited=true、exhausted=true。','commentsCollected=0 且 noteContexts=[]，classificationCounts={}。','filterCounts 中 duplicate=5、outside_time_range=1；failures=[]。'],
      recommended_actions:['补充 noteContexts 与逐条 reason，核对 timeFilter。'],risks:['endReasons 为 no_more_results；workbuddy 相关需求仍须核对原文。']}};
  await page.evaluate(result=>{currentTaskId='6';renderTaskPanel(result);render(result);switchResultTab('report');},readableReport);
  const reportPanel = page.locator('[data-result-panel="report"]');
  assert.equal(await reportPanel.isVisible(),true);
  const reportText = await reportPanel.innerText();
  assert.doesNotMatch(reportText,/\b(?:xhs|topLeads|noteContexts|qualifiedCount|leadsCount|visited|exhausted|true|commentsCollected|classificationCounts|filterCounts|duplicate|outside_time_range|failures|endReasons|no_more_results|reason|timeFilter)\b/);
  assert.match(reportText,/重复内容为 5 条、超出时间范围的内容为 1 条/);
  assert.match(reportText,/workbuddy 相关需求仍须核对原文/);
  assert.match(reportText,/不能判定无需求/);
  assert.equal(await page.evaluate(()=>cachedData.report.summary),readableReport.report.summary,'rendering a Chinese report must retain the original model output');
  await page.screenshot({path:'/tmp/workbench-ui-chinese-report-smoke.png',fullPage:true});
  const businessContact = {taskId:7,status:'completed',isRunning:false,notes:[{id:'source-contact',title:'商机来源',author:'示例作者',content:'联系13812345678或a@example.com',url:'https://example.test/source?note_id=13812345678&xsec_token=fixture-token'}],
    leads:{leads:[{lead_id:'contact',classification:'demand',nickname:'示例联系人',user_id:'12345678901234567890',phone:'13812345678',email:'a@example.com',evidence:'需要开发服务',content:'需要开发服务，请联系13812345678或a@example.com',outreach_copy:'联系13812345678或a@example.com；api_key=fixture-key',note_url:'https://example.test/source?note_id=13812345678&xsec_token=fixture-token',review:{status:'approved'}}]}};
  await page.evaluate(result=>{currentTaskId='7';renderTaskPanel(result);render(result);switchResultTab('leads');},businessContact);
  if (await page.locator('.wb-progress-disclosure').evaluate(element=>element.open)) await page.locator('.wb-progress-disclosure > summary').click();
  await page.locator('#wbLeadRows').scrollIntoViewIfNeeded();
  await page.locator('[data-wb-lead="contact"]').click();
  await page.getByText('完整来源内容',{exact:true}).click();
  const contactText=await page.locator('.wb-active-lead').innerText();
  assert.match(contactText,/示例联系人.*12345678901234567890/s);
  assert.match(contactText,/13812345678或a@example.com/);
  assert.doesNotMatch(contactText,/fixture-key|fixture-token|手机已脱敏|邮箱已脱敏/);
  const contactExports=await page.evaluate(()=>{
    const originalExcel=OfflineExcel.download, originalText=downloadText, output={};
    try { OfflineExcel.download=sheets=>{output.sheets=sheets;}; downloadText=text=>{output.markdown=text;}; exportScope='all';exportExcel();exportMarkdown(); }
    finally { OfflineExcel.download=originalExcel; downloadText=originalText; }
    return output;
  });
  assert.match(contactExports.markdown,/13812345678或a@example.com/);
  assert.equal(contactExports.sheets[1].rows[0]['作者'],'示例作者');
  assert.doesNotMatch(JSON.stringify(contactExports),/fixture-key|fixture-token/);
  await page.locator('#wbLeadDetail').evaluate(element=>{element.scrollTop=0;});
  await page.locator('.wb-review-split').screenshot({path:'/tmp/workbench-ui-business-contacts.png'});
  await page.goto(`http://127.0.0.1:${server.address().port}/harness.html`);
  await page.locator('#harnessMain').waitFor({ state: 'visible' });
  await page.evaluate(() => {
    document.getElementById('nav-settings').classList.add('is-pressing');
    document.dispatchEvent(new Event('pointerdown'));
    document.dispatchEvent(new Event('pointerleave'));
  });
  assert.equal(await page.locator('button.is-pressing').count(), 0);
  await page.click('#nav-evaluations'); await page.locator('#evaluationPanel').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#evalDataset option').count(), 1);
  assert.equal(await page.locator('#settingBusinessDirection').count(), 0);
  await page.evaluate(result => renderTask({ taskId: 2, status: 'partial', result }), focusedResult);
  assert.match(await page.locator('#harnessStageCoverage').textContent(), /质量核验：失败/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(), /2 \/ 3 篇笔记/);
  await page.evaluate(result=>renderTask({taskId:5,status:'partial',result}),collectionResult);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/发布时间仍待核验 7/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/额外补读详情 9/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/7 条发布时间待核验，不能据此判断没有需求/);
  await page.evaluate(() => renderTask({ taskId: 3, status: 'partial', result: { notes: [{ title: '历史笔记' }], qa: {}, report: {}, leads: { leads: [] } } }));
  assert.match(await page.locator('#harnessStageCoverage').textContent(), /通用关键词研究/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(), /未见已保存的质检产物，未确认完成/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/旧任务未记录过滤明细/);
  assert.doesNotMatch(await page.locator('#harnessStageCoverage').textContent(),/发布时间仍待核验 7/);
  await page.evaluate(() => {
    currentTaskId = '3'; latestTask = {taskId:3,status:'completed',isRunning:false,qa:{approved:false},stageCoverage:{qa:{status:'completed'}},leads:{leads:[{qa_status:'needs_review',review:{status:'pending'}}]},evolution:{status:'awaiting_evaluation',candidatePromptIds:[91],candidateStates:[{promptId:91,status:'candidate',evaluationState:'pending_human_review'}]}};
    renderTask(latestTask); setActivePage('overview');
  });
  await page.locator('#adminTaskInsights').waitFor({state:'visible'});
  const insightText=await page.locator('#adminTaskInsights').textContent();
  assert.match(insightText,/执行已完成/); assert.match(insightText,/质检需复核/); assert.match(insightText,/1 条待人工审核/); assert.match(insightText,/待人工验收/);
  await page.screenshot({path:'/tmp/workbench-ui-evolution-smoke.png',fullPage:true});
  await page.evaluate(async () => {
    setActivePage('evaluations');
    await displayEvaluationResult({runId:'qa-mock',datasetId:'qa-mock-dataset',agentKey:'critic_qa_agent',evaluationKind:'qa',split:'acceptance',autoPassed:true,passed:false,humanReviewComplete:false,requiredHumanChecks:['问题归因与严重度正确'],metrics:{machineChecksPassed:1,failedSamples:0},rows:[{id:'qa-1',inputText:'定制开发询价的公开原文（测试样本）',inputContext:{leads:[{lead_id:'lead-1',evidence:'询价原文'}]},expected:{approved:false,issueTargets:['lead-1']},prediction:{approved:false,issues:[{target_id:'lead-1',level:'warning',message:'委托范围待确认'}]},machineChecksPassed:true,citationPassed:null,fabricationPassed:null}]});
  });
  assert.equal(await page.getByLabel('本条核验依据（必填）',{exact:true}).isVisible(),true);
  assert.equal(await page.getByLabel('结论（必选）',{exact:true}).inputValue(),'');
  await page.getByLabel('结论（必选）',{exact:true}).selectOption('fail');
  await page.getByLabel('本条核验依据（必填）',{exact:true}).fill('已检查原文，问题归因存在偏差。');
  assert.deepEqual(await page.evaluate(()=>evaluationQualityReviews.get('qa-1')),{decision:'fail',note:'已检查原文，问题归因存在偏差。'});
  assert.match(await page.locator('#evalComparison').textContent(),/引用与事实仍须人工核验/);
  await page.screenshot({path:'/tmp/workbench-ui-specialised-eval-smoke.png',fullPage:true});
  await page.click('#nav-settings');
  await page.locator('#page-settings').waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.getElementById('settingTotalTimeout').value === '300');
  const timeoutInputs = [
    ['settingConnectTimeout', '模型连接超时（秒）', '30'],
    ['settingReadTimeout', '首响应与流空闲超时（秒）', '60'],
    ['settingTotalTimeout', '本次分析总时限（秒）', '300'],
  ];
  for (const [id, label, expected] of timeoutInputs) {
    const input = page.getByLabel(label, { exact: true });
    assert.equal(await input.getAttribute('id'), id);
    assert.equal(await input.isVisible(), true);
    assert.equal(await input.isEditable(), true);
    assert.equal(await input.inputValue(), expected);
    assert.equal(await input.getAttribute('min'), '1');
    assert.equal(await input.getAttribute('max'), '86400');
    assert.equal(await input.getAttribute('step'), '1');
    assert.equal(await page.locator(`label[for="${id}"]`).evaluate(label => label.scrollWidth <= label.clientWidth), true, `${id} label must fit`);
  }
  assert.equal(settingsCount, 1);
  assert.match(await page.locator('#page-settings').textContent(), /相邻数据的最长间隔/);
  assert.match(await page.locator('#page-settings').textContent(), /不在重试时重置/);
  await page.screenshot({ path: process.env.WORKBENCH_SETTINGS_SCREENSHOT || '/tmp/workbench-ui-settings-smoke.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log('PASS: isolated Chrome mocked UI login, editable plan, custom limits, review, outreach unknown state, focused demand filters with original-index actions, stage coverage in both pages, conservative legacy display, eval gate and model timeout settings.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); server.close(); });
