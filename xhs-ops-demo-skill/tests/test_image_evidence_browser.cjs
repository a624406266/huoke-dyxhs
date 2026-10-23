/* Isolated screenshot/UI test; mock records only, all external and write requests blocked. */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require('playwright');
const { makeState, createServer, routeApi } = require('./workspace_mock_server.cjs');
const state = makeState(), task = state.tasks[0];
const parent = {title:'图片需求来源',desc:'#开发[话题]#',tags:['智能体'],images:1,image_status:'complete',image_evidence:[{
  index:1,status:'recognized',text:'需要门店客服智能体，找人开发。<script>不能执行</script>',visual_description:'一张需求文字海报',
  uncertain_regions:[],url:'https://sns-webpic-qc.xhscdn.com/fixture.png'
}]};
for (const lead of [...task.leads.leads, ...task.result.leads.leads]) lead.source_comment = {note:parent};
const original = JSON.stringify(state.tasks), server = createServer(state);
let browser;
(async () => {
  await new Promise((resolve,reject) => {server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const origin = `http://127.0.0.1:${server.address().port}`, writes=[], external=[], errors=[];
  browser = await chromium.launch({headless:true,chromiumSandbox:true,
    ...(process.env.WORKBENCH_CHROME ? {executablePath:process.env.WORKBENCH_CHROME} : {})});
  const context = await browser.newContext({viewport:{width:1440,height:1100},serviceWorkers:'block'});
  const postReads = new Set(['/api/tasks/list','/api/tasks/status','/api/admin/tasks/list','/api/admin/tasks/status','/api/admin/tasks/audit']);
  await context.route('**/*',route => {
    const request=route.request(),url=new URL(request.url());
    if(url.origin!==origin){external.push(request.url());return route.abort();}
    if(request.method()==='POST' && postReads.has(url.pathname)) return route.fulfill({status:200,json:{ok:true,data:routeApi(state,url.pathname,url.searchParams,request.postDataJSON()||{},'POST')}});
    if(request.method()!=='GET'){writes.push(request.url());return route.abort();}
    return route.continue();
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`${origin}/index.html#/reviews?task=${task.taskId}&tab=leads&lead=${task.leads.leads[0].lead_id}`);
  const summary=page.locator('summary').filter({hasText:'父笔记与图片内容'}).first();
  await summary.waitFor({state:'visible'});await summary.click();
  const evidence=summary.locator('..');
  assert.match(await evidence.innerText(),/图片需求来源[\s\S]*#开发\[话题\]#[\s\S]*智能体[\s\S]*文字已识别[\s\S]*需要门店客服智能体/);
  assert.match(await evidence.innerText(),/<script>不能执行<\/script>/);
  assert.equal(await evidence.locator('script').count(),0);
  assert.equal(await evidence.getByRole('link',{name:'查看原图 ↗'}).getAttribute('href'),parent.image_evidence[0].url);
  await page.screenshot({path:path.join(os.tmpdir(),'huoke-image-evidence-ui.png')});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
  assert.deepEqual(writes,[]);assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
  assert.equal(JSON.stringify(state.tasks),original);
  console.log('PASS: parent title/body/tags, image text, original image link, escaping, mobile width; no writes/external calls.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));});
