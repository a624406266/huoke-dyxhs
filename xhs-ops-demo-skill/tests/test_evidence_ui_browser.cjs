/* Isolated, read-only browser evidence check. Uses only the in-memory fixture;
 * read APIs using POST are fulfilled in memory; writes and external origins are blocked.
 */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require('playwright');
const { makeState, createServer, routeApi } = require('./workspace_mock_server.cjs');
const clone = value => JSON.parse(JSON.stringify(value));
const state = makeState(), base = clone(state.tasks[0]);
const fullContent = {mode:'full_text',requested:3,attempted:3,fetched:3,complete:3,excluded:0,missing:0,failed:0,pending:0,gaps:[]};
function task(id, status, content) {
  const value = clone(base);
  const notes = Array.from({length:3}, (_, index) => ({id:`evidence-${index + 1}`,platform:'xhs',title:`隔离来源 ${index + 1}`,content:'隔离示例正文，没有明确采购意向。',comments:0}));
  const stages = Object.fromEntries(['search','details','relevance','comments','lead_signal','qa','report'].map(key => [key, {
    status:key === 'qa' ? 'skipped' : 'completed',completed:key === 'qa' ? 0 : key === 'report' ? 1 : 3,
    total:key === 'qa' ? 0 : key === 'report' ? 1 : 3,unit:key === 'qa' ? 'candidate' : key === 'report' ? 'report' : key === 'search' ? 'query' : 'note',
    ...(key === 'qa' ? {reason:'没有候选需要质检。'} : {})
  }]));
  const plan = {...value.confirmedPlan,offeringId:'general',searchGoal:'检查正文证据覆盖',keywords:['隔离测试'],excludeKeywords:[]};
  const summary = {notesCollected:3,commentsCollected:0,contentCoverage:clone(content),filterCounts:{invalid_record:0,non_note:0,missing_note_id:0,duplicate:0,excluded_keyword:0,outside_time_range:0,unknown_publish_time:0,capacity_limit:0},timeResolution:{attempted:0,resolved:0,unresolved:0,failed:0},timeReferenceAt:'2026-10-21T10:00:00Z'};
  const result = {taskId:id,confirmedPlan:plan,searchGoal:plan.searchGoal,notes,leads:{leads:[],totalCommentsScanned:0},qa:{},report:{summary:status === 'partial' ? '正文核验存在缺口，本次尚不能得出完整结论。' : '本次未发现符合条件的线索。'},stageCoverage:stages,collectionSummary:summary};
  Object.assign(value,{id,taskId:id,status,isRunning:false,keyword:`正文覆盖隔离检查 ${id}`,statusText:status === 'partial' ? '部分完成：正文核验有缺口' : '已完成，本次未发现符合条件的线索',confirmedPlan:plan,notes,leads:result.leads,notesCount:3,leadsCount:0,stageCoverage:stages,collectionSummary:summary,result,agentTrace:[],events:[],evolution:{status:'no_changes'},taskSummary:{task_summary:result.report.summary}});
  return value;
}
const partial = task(6,'partial',{...fullContent,fetched:2,complete:1,missing:2,failed:1,gaps:[{noteId:'evidence-2',platform:'xhs',reason:'详情未提供可用正文，不能仅凭标题确认无关。'},{noteId:'evidence-3',platform:'xhs',reason:'详情读取失败，需要补充核验。'}]});
partial.stageCoverage.details = {status:'partial',completed:1,total:3,unit:'note',reason:'2 篇笔记正文尚未完整核验。'};
partial.notes[1].content = ''; partial.notes[2].content = '';
const emptyComplete = task(7,'completed',fullContent);
const excluded = task(8,'completed',{...fullContent,requested:1,attempted:1,fetched:1,complete:0,excluded:1});
excluded.notes = []; excluded.result.notes = []; excluded.notesCount = 0; excluded.collectionSummary.notesCollected = 0;
excluded.collectionSummary.filterCounts.outside_time_range = 1;
excluded.collectionSummary.zeroResultReason = '详情确认 1 篇来源超出所选时间范围，已核实排除。';
excluded.stageCoverage.details = {status:'completed',completed:1,total:1,unit:'note'};
for (const key of ['relevance','comments','lead_signal']) excluded.stageCoverage[key] = {status:'skipped',completed:0,total:0,unit:'note',reason:'没有符合所选时间范围的来源。'};
const legacy = task(9,'completed',fullContent);
delete legacy.collectionSummary.contentCoverage;
legacy.stageCoverage.details = {status:'completed',completed:0,total:0,unit:'note'};
state.tasks = [partial,emptyComplete,excluded,legacy];
const originalTasks = JSON.stringify(state.tasks), server = createServer(state);
let browser;
(async () => {
  await new Promise((resolve,reject) => { server.once('error',reject); server.listen(0,'127.0.0.1',resolve); });
  const origin = `http://127.0.0.1:${server.address().port}`, writes = [], external = [], errors = [];
  browser = await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.WORKBENCH_CHROME ? {executablePath:process.env.WORKBENCH_CHROME} : {})});
  const context = await browser.newContext({viewport:{width:1440,height:1180},serviceWorkers:'block'});
  const postReads = new Set(['/api/tasks/list','/api/tasks/status','/api/admin/tasks/list','/api/admin/tasks/status','/api/admin/tasks/audit']);
  await context.route('**/*',route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { external.push(request.url()); return route.abort(); }
    if (request.method() === 'POST' && postReads.has(url.pathname)) return route.fulfill({status:200,json:{ok:true,data:routeApi(state,url.pathname,url.searchParams,request.postDataJSON() || {},'POST')}});
    if (request.method() !== 'GET') { writes.push(request.method() + ' ' + request.url()); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage(); page.on('pageerror',error => errors.push(error.message));
  async function openTask(id) {
    await page.goto(`${origin}/index.html#/reviews?task=${id}`);
    await page.locator('#salesMain').waitFor({state:'visible'});
    await page.waitForFunction(taskId => document.getElementById('wbReviewContext')?.textContent.includes(`任务 #${taskId} ·`),id);
    await page.locator('#wbTaskInsights').waitFor({state:'visible'});
    const disclosure = page.locator('.wb-progress-disclosure');
    if (!await disclosure.evaluate(element => element.open)) await disclosure.locator('> summary').click();
    await page.locator('#stageCoverage .wb-task-explanation').waitFor({state:'visible'});
  }
  async function screenshot(name) {
    const file = path.join(os.tmpdir(),`evidence-ui-${name}.png`);
    await page.locator('#taskPanel').screenshot({path:file});
    console.log(`SCREENSHOT: ${file}`);
  }
  await openTask(6);
  assert.match(await page.locator('#wbTaskInsights').innerText(),/部分结果可用[\s\S]*正文核验仍有缺口/);
  const partialText = await page.locator('#stageCoverage').innerText();
  assert.match(partialText,/正文完整 1 \/ 3 篇/);
  assert.match(partialText,/读取内容：2 条尚未完成/);
  assert.doesNotMatch(partialText,/质量核验|模型质检|版本改进|0 \/ 0/);
  await screenshot('partial');
  await openTask(7);
  assert.match(await page.locator('#wbTaskInsights').innerText(),/任务已完成[\s\S]*本次未发现符合条件的线索/);
  assert.match(await page.locator('#stageCoverage').innerText(),/正文完整 3 \/ 3 篇/);
  await screenshot('complete-zero');
  await openTask(8);
  assert.match(await page.locator('#wbTaskInsights').innerText(),/本次未发现符合条件的线索/);
  assert.match(await page.locator('#stageCoverage').innerText(),/正文完整 0 \/ 1 篇/);
  assert.match(await page.locator('#stageCoverage').innerText(),/1 条内容超出所选时间范围/);
  assert.doesNotMatch(await page.locator('#wbTaskInsights').innerText(),/正文核验仍有缺口/);
  await screenshot('excluded');
  await openTask(9);
  assert.match(await page.locator('#stageCoverage').innerText(),/未记录正文完整性/);
  assert.doesNotMatch(await page.locator('#wbTaskInsights').innerText(),/本次未发现符合条件的线索/);
  await screenshot('legacy');
  await page.goto(`${origin}/harness.html`);
  await page.locator('#harnessMain').waitFor({state:'visible'});
  await page.evaluate(data => {renderTask(data); setActivePage('overview');},partial);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/正文完整 1 · 已核实排除 0 · 正文缺失 2/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/质量核验：未执行[\s\S]*没有候选需要质检/);
  const adminText = await page.locator('#harnessStageCoverage').textContent();
  assert.match(adminText,/evidence-2：详情未提供可用正文/);
  assert.match(adminText,/evidence-3：详情读取失败/);
  assert.ok(adminText.indexOf('笔记详情：') < adminText.indexOf('相关性研判：'),'both pages must show the same content-first order');
  await page.evaluate(data => renderTask(data),excluded);
  assert.match(await page.locator('#adminTaskInsights').textContent(),/本次未发现符合条件的线索/);
  assert.match(await page.locator('#harnessStageCoverage').textContent(),/已核实排除 1/);
  assert.deepEqual(writes,[],'read-only evidence verification must not submit writes');
  assert.deepEqual(external,[],'all observed requests must remain on the isolated fixture');
  assert.deepEqual(errors,[],'both UI pages must render without browser errors');
  assert.equal(JSON.stringify(state.tasks),originalTasks,'display cannot mutate even mock task history');
  console.log('PASS: isolated read-only Chrome evidence coverage, gap reasons, zero-lead completion, time exclusions, historical uncertainty, and both UI pages.');
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {await browser?.close(); await new Promise(resolve => server.close(resolve));});
