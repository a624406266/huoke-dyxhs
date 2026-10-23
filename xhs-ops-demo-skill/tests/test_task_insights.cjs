/* Pure display decisions: no real model, API, account or task data. */
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const scope = {console}; vm.createContext(scope);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../assets/task-insights.js'),'utf8'),scope);
const ui = scope.TaskInsights;
const completed = {taskId:7,status:'completed',stageCoverage:{qa:{status:'completed'}},qa:{approved:false},leads:{leads:[{qa_status:'needs_review',review:{status:'pending'},business_review_required:true}]}};
const html = ui.summaryHtml(completed);
assert.match(html,/执行已完成/); assert.match(html,/质检需复核/); assert.match(html,/1 条待人工审核/); assert.match(html,/未复盘/);
const partialDecisions = {status:'partial',progress:{stage:'partial',percent:0},stageCoverage:{
  relevance:{status:'partial',completed:26,total:28,unit:'note'},
  comments:{status:'skipped',completed:0,total:10,unit:'note',reason:'已达到评论预算'},
  qa:{status:'completed',completed:51,total:51,unit:'candidate'}},
  collectionSummary:{endReasons:['note_quota_reached'],failures:[{stage:'relevance',source:'n1',error:'模型没有返回判断'},{stage:'relevance',source:'n2',error:'模型没有返回判断'}]},
  leads:{qualifiedCount:0,leads:[{qa_status:'needs_review',business_review_required:true}]}};
const partialSnapshot = JSON.stringify(partialDecisions), partialSummary = ui.summaryHtml(partialDecisions);
assert.match(partialSummary,/流程已结束 · 执行部分完成/);
assert.match(partialSummary,/相关性判断有 2 条未完成（26\/28）/);
assert.match(partialSummary,/可查看已有结果/);
assert.doesNotMatch(ui.execution(partialDecisions).detail,/评论读取|模型质检|记录 2 项异常/,'budgets, business review and repeated failure records must not create additional execution gaps');
assert.equal(ui.execution(partialDecisions).terminal,true);
assert.doesNotMatch(ui.execution(partialDecisions).progressText,/0%|100%/,'finished partial work has no misleading completion percentage');
assert.equal(ui.execution({status:'partial',result:{...partialDecisions}}).detail,ui.execution(partialDecisions).detail);
assert.equal(JSON.stringify(partialDecisions),partialSnapshot,'derived explanations must preserve historical task data');
const businessSummary = ui.workbenchSummaryHtml({...partialDecisions,evolution:{status:'failed',error:'internal failure'}});
assert.match(businessSummary,/部分结果可用[\s\S]*分析相关内容：2 条尚未完成/);
assert.doesNotMatch(businessSummary,/模型质检|人工审核已记录|版本改进|internal failure/);
assert.match(ui.summaryHtml(partialDecisions),/模型质检[\s\S]*人工审核[\s\S]*版本改进/,'admin diagnostics retain independent states');
assert.match(ui.workbenchSummaryHtml({status:'needs_action'}),/需要处理[\s\S]*联系维护人员/);
assert.match(ui.workbenchSummaryHtml({status:'failed'}),/任务未完成/);
assert.match(ui.execution({status:'partial',collectionSummary:{failures:[{stage:'read_detail',error:'unavailable'}]}}).detail,/正文读取记录 1 项异常/);
assert.match(ui.execution({status:'partial'}).detail,/证据或分析缺口/,'missing diagnostics must not invent a cause');
assert.equal(ui.execution({...partialDecisions,status:'completed'}).detail,'','business outcomes cannot override execution status');
assert.doesNotMatch(html,/版本改进<\/span><strong[^>]*>已启用/);
assert.equal(ui.evolution({...completed,taskSummary:{evolution_summary:'旧摘要宣称优化成功'},evolutionSummary:'自动优化好了'}).label,'未复盘');
assert.equal(ui.quality({...completed,stageCoverage:{qa:{status:'partial'}},qa:{approved:true}}).label,'质检尚未完成');
assert.equal(ui.quality({leads:{leads:[]},stageCoverage:{qa:{status:'completed'}}}).label,'无候选待质检');
assert.equal(ui.human({...completed,leads:{leads:[{qa_status:'approved'}]}}).label,'1 条待人工审核');
const task = {...completed,evolution:{status:'awaiting_evaluation',candidatePromptIds:[11]}};
const candidate = {id:11,sourceTaskId:7,status:'candidate'};
assert.equal(ui.evolution(task,[candidate]).label,'建议候选 · 待评测');
assert.equal(ui.evolution(task,[candidate],[{promptId:99,split:'acceptance',passed:true,humanReviewComplete:true}]).label,'建议候选 · 待评测','other prompt proof cannot imply activation');
assert.equal(ui.evolution(task,[candidate],[{promptId:11,split:'development',autoPassed:true}]).label,'建议候选 · 待评测','development proof is not acceptance');
assert.equal(ui.evolution(task,[candidate],[{promptId:11,split:'acceptance',autoPassed:true}]).label,'待人工验收');
assert.equal(ui.evolution(task,[candidate],[{promptId:11,split:'acceptance',passed:true,humanReviewComplete:true}]).label,'待启用');
assert.equal(ui.evolution(task,[{...candidate,status:'active'}]).label,'已启用');
assert.equal(ui.evolution({...task,isEvolving:true},[{...candidate,status:'active'}]).label,'正在复盘');
assert.equal(ui.evolution({...task,evolution:{status:'failed',error:'<script>x</script>'}}).label,'复盘失败');
assert.doesNotMatch(ui.summaryHtml({...task,evolution:{status:'failed',error:'<script>x</script>'}}),/<script>/);
assert.equal(ui.evolution({...task,evolution:{status:'awaiting_evaluation',candidatePromptIds:[11],candidateStates:[{promptId:11,status:'candidate',evaluationState:'pending_human_review'}]}}).label,'待人工验收');
assert.equal(ui.evolution({...task,evolution:{status:'awaiting_evaluation',candidatePromptIds:[11],candidateStates:[{promptId:11,status:'candidate',evaluationState:'not_evaluated'}]}},[candidate],[{promptId:11,split:'acceptance',passed:true,humanReviewComplete:true}]).label,'建议候选 · 待评测','server hash/scope/policy gates beat stale local proof');
const uncertain = {classification:'uncertain',qualified:false,score:33,review_priority:'high',inquiry_type:'price_inquiry',missing_information:['交付范围','<img onerror=x>'],business_review_required:true};
assert.equal(ui.inquiry(uncertain).priorityLabel,'优先核验');
assert.equal(ui.inquiry(uncertain).rank,0); assert.equal(uncertain.score,33); assert.equal(uncertain.qualified,false);
assert.match(ui.inquiryHtml(uncertain),/尚未等同于合格委托/); assert.match(ui.inquiryHtml(uncertain),/交付范围/); assert.doesNotMatch(ui.inquiryHtml(uncertain),/<img/);
assert.equal(ui.inquiry({...uncertain,classification:'promotion'}).priority,'none','supplier promotion is never inquiry-prioritized');
assert.equal(ui.inquiry({...uncertain,classification:'demand',qualified:true,qa_status:'needs_review'}).pending,true,'known unapproved QA leaves historic qualification pending without changing stored data');

// Collection diagnostics report persisted reasons rather than infer them from
// the scanned total. Unix seconds and ISO references must show the same instant.
const collection = {taskId:5,status:'partial',notes:[],collectionSummary:{notesCollected:0,
  filterCounts:{invalid_record:1,non_note:2,missing_note_id:3,duplicate:4,excluded_keyword:5,outside_time_range:6,unknown_publish_time:7,capacity_limit:0},
  timeResolution:{attempted:9,resolved:2,unresolved:7,failed:1},timeReferenceAt:1792555200,
  zeroResultReason:'7 条发布时间待核验；<img onerror="bad">不能据此认定没有需求。'}};
const snapshot = JSON.stringify(collection), diagnostics = ui.collectionHtml(collection);
for (const [label,value] of [['无效记录',1],['非笔记内容',2],['缺少笔记编号',3],['重复内容',4],['命中排除关键词',5],['发布时间超出范围',6],['发布时间仍待核验',7],['达到采集上限',0]]) {
  assert.ok(diagnostics.includes(`${label} <b>${value}</b>`),`${label} must use its own recorded count, including zero`);
}
assert.match(diagnostics,/额外补读详情 9 · 已确认时间 2 · 仍待核验 7 · 补证失败 1/);
assert.match(diagnostics,/不包含正常内容详情读取/); assert.match(diagnostics,/本轮冻结/);
assert.match(diagnostics,/2026-10-21 12:00:00.000 北京时间（UTC\+08:00）/);
assert.match(diagnostics,/无结果说明：.*7 条发布时间待核验/);
assert.match(diagnostics,/&lt;img onerror=/); assert.doesNotMatch(diagnostics,/<img/);
assert.match(diagnostics,/发布时间待核验不等于没有需求/);
assert.match(diagnostics,/未知发布时间的内容不能认定为满足所选时间范围/);
assert.equal(JSON.stringify(collection),snapshot,'diagnostics must not edit task history');
assert.equal(ui.collectionHtml({status:'partial',result:{notes:[],collectionSummary:{...collection.collectionSummary,timeReferenceAt:'2026-10-21T04:00:00.000Z'}}}),diagnostics,'admin nested result and workbench flattened result must agree');
assert.equal(ui.collectionHtml({...collection,collectionSummary:{...collection.collectionSummary,timeReferenceAt:'2026-10-21T12:00:00+08:00'}}),diagnostics,'an ISO offset must be applied only once');
assert.match(ui.collectionHtml({collectionSummary:{timeReferenceAt:'2026-10-20T20:30:00Z'}}),/2026-10-21 04:30:00.000 北京时间（UTC\+08:00）/,'Beijing day rollover must follow the stored instant');
const legacy = ui.collectionHtml({taskId:5,status:'completed',notes:[],collectionSummary:{notesCollected:0,rawSeen:122}});
assert.match(legacy,/旧任务未记录过滤明细/); assert.match(legacy,/不能根据已扫描总数反推各类过滤原因/);
assert.match(legacy,/未记录详情补证统计/); assert.match(legacy,/未记录无结果原因，不能据此认定没有需求/);
assert.doesNotMatch(legacy,/122|仍待核验 <b>\d|无效记录 <b>\d/);
const missing = ui.collectionHtml({status:'partial',collectionSummary:{filterCounts:{duplicate:0,outside_time_range:-1,unknown_publish_time:'9'},timeResolution:{attempted:3},timeReferenceAt:'not a date'}});
assert.match(missing,/重复内容 <b>0/); assert.match(missing,/无效记录 <b>未记录/);
assert.match(missing,/发布时间超出范围 <b>未记录/); assert.match(missing,/发布时间仍待核验 <b>未记录/);
assert.match(missing,/额外补读详情 3 · 已确认时间 未记录/); assert.match(missing,/格式无法识别/);
const running = ui.collectionHtml({status:'running',isRunning:true,notes:[]});
assert.match(running,/尚未记录过滤明细/); assert.doesNotMatch(running,/旧任务|无结果说明/);
const batch = ui.collectionHtml({collectionSummary:{...collection.collectionSummary,filterCountsScope:'executed_rounds',timeReferences:[{round:1,timeReferenceAt:1792555200},{round:2,timeReferenceAt:'2026-10-21T05:00:00Z'}]}});
assert.match(batch,/按已执行轮次累计/); assert.match(batch,/各轮分别冻结/);
assert.match(batch,/第 1 轮：2026-10-21 12:00:00.000 北京时间（UTC\+08:00）；第 2 轮：2026-10-21 13:00:00.000 北京时间（UTC\+08:00）/);
const interrupted = ui.collectionHtml({status:'needs_action',collectionSummary:{...collection.collectionSummary,unprocessedNotes:3,timeResolution:{attempted:2,resolved:1,unresolved:0,failed:0,interrupted:1}}});
assert.match(interrupted,/已返回但尚未处理／纳入：<\/b>3 条/);
assert.match(interrupted,/仍待核验 0 · 补证失败 0 · 补证中断 1/,'interrupted resolution must not be added to failed or unresolved counts');
assert.doesNotMatch(legacy,/已返回但尚未处理／纳入|补证中断/,'missing legacy counters cannot be inferred from raw seen totals');
assert.doesNotMatch(diagnostics,/已返回但尚未处理／纳入|补证中断/,'otherwise recorded diagnostics do not imply new counters were recorded');
const noInterruptions = ui.collectionHtml({collectionSummary:{unprocessedNotes:0,timeResolution:{interrupted:0}}});
assert.match(noInterruptions,/已返回但尚未处理／纳入：<\/b>0 条/); assert.match(noInterruptions,/补证中断 0/,'an explicit zero is a recorded value');
const invalidInterruptions = ui.collectionHtml({collectionSummary:{unprocessedNotes:-1,timeResolution:{interrupted:'<img>'}}});
assert.match(invalidInterruptions,/已返回但尚未处理／纳入：<\/b>未记录<\/p>/); assert.match(invalidInterruptions,/补证中断 未记录/);
assert.doesNotMatch(invalidInterruptions,/<img>/);

// Full content verification distinguishes no findings from unexamined sources.
const fullContent = {mode:'full_text',requested:15,attempted:15,fetched:15,complete:15,missing:0,failed:0,pending:0,gaps:[]};
const emptyComplete = {status:'completed',leads:{leads:[]},collectionSummary:{contentCoverage:fullContent}};
assert.match(ui.summaryHtml(emptyComplete),/本次未发现符合条件的线索/);
assert.match(ui.workbenchSummaryHtml(emptyComplete),/本次未发现符合条件的线索/);
assert.doesNotMatch(ui.workbenchSummaryHtml({status:'completed',leads:{leads:[]}}),/本次未发现符合条件的线索/);
assert.match(ui.collectionHtml(emptyComplete),/计划读取 15 · 已尝试 15 · 已取得详情 15 · 正文完整 15 · 正文缺失 0 · 读取失败 0 · 尚未处理 0/);
const outsideWindow = {...emptyComplete,collectionSummary:{contentCoverage:{...fullContent,requested:1,attempted:1,fetched:1,complete:0,excluded:1}}};
assert.match(ui.summaryHtml(outsideWindow),/本次未发现符合条件的线索/,'detail-confirmed time exclusions finish the planned verification without full text');
assert.match(ui.collectionHtml(outsideWindow),/正文完整 0 · 已核实排除 1 · 正文缺失 0 · 读取失败 0/);
assert.doesNotMatch(ui.summaryHtml(outsideWindow),/正文核验仍有缺口/);
assert.doesNotMatch(ui.collectionHtml(emptyComplete),/已核实排除/,'old records with no excluded counter must not acquire a fabricated display counter');
assert.doesNotMatch(ui.summaryHtml({...outsideWindow,collectionSummary:{contentCoverage:{...outsideWindow.collectionSummary.contentCoverage,excluded:'1'}}}),/本次未发现符合条件的线索/,'invalid exclusion counters cannot establish complete verification');
assert.doesNotMatch(ui.summaryHtml({...emptyComplete,status:'running'}),/本次未发现符合条件的线索/);
assert.doesNotMatch(ui.summaryHtml({status:'completed',leads:{leads:[]}}),/本次未发现符合条件的线索/,'historical completion cannot establish text verification');
assert.doesNotMatch(ui.summaryHtml({status:'completed',collectionSummary:emptyComplete.collectionSummary}),/本次未发现符合条件的线索/,'missing results cannot establish zero findings');
const incompleteContent = {...fullContent,attempted:14,fetched:13,complete:12,missing:2,failed:1,pending:1,gaps:[{noteId:'n1<img>',platform:'xhs',reason:'只有标题，正文为空<script>x</script>'},{noteId:'n2',platform:'douyin',reason:'详情读取失败'},{noteId:'n3',platform:'xhs',reason:'尚未处理'}]};
const partialContent = {...emptyComplete,status:'partial',collectionSummary:{contentCoverage:incompleteContent}}, originalContent = JSON.stringify(partialContent);
assert.match(ui.workbenchSummaryHtml(partialContent),/正文核验仍有缺口/);
assert.match(ui.workbenchDetailsHtml(partialContent),/正文完整 12 \/ 15 篇/);
assert.doesNotMatch(ui.workbenchSummaryHtml(partialContent),/本次未发现符合条件的线索/);
assert.equal(JSON.stringify(partialContent),originalContent,'business summaries must preserve recorded evidence');
assert.match(ui.summaryHtml(partialContent),/执行部分完成/); assert.match(ui.summaryHtml(partialContent),/正文核验仍有缺口/);
assert.doesNotMatch(ui.summaryHtml(partialContent),/本次未发现符合条件的线索/);
const contentDiagnostics = ui.collectionHtml(partialContent);
assert.match(contentDiagnostics,/正文完整 12 · 正文缺失 2 · 读取失败 1 · 尚未处理 1/);
assert.match(contentDiagnostics,/查看 3 条正文核验缺口/); assert.match(contentDiagnostics,/小红书 · 笔记 n1&lt;img&gt;：只有标题，正文为空&lt;script&gt;/);
assert.match(contentDiagnostics,/抖音 · 笔记 n2：详情读取失败/); assert.doesNotMatch(contentDiagnostics,/<script>|<img>/);
assert.equal(ui.collectionHtml({status:'partial',result:{leads:partialContent.leads,collectionSummary:partialContent.collectionSummary}}),contentDiagnostics,'both views must show the same persisted content evidence');
assert.equal(JSON.stringify(partialContent),originalContent,'content diagnostics must preserve task history');
assert.match(legacy,/未记录正文读取与完整性/); assert.doesNotMatch(legacy,/正文完整 \d|正文缺失 \d|只有标题/,'legacy evidence cannot be reconstructed from completion or note totals');
const summaryOnly = {...emptyComplete,collectionSummary:{contentCoverage:{...fullContent,mode:'summary',requested:0,attempted:0,fetched:0,complete:0,missing:15}}};
assert.match(ui.collectionHtml(summaryOnly),/摘要模式，正文未完整核验/); assert.match(ui.collectionHtml(summaryOnly),/正文缺失 15 · 读取失败 0/);
assert.doesNotMatch(ui.summaryHtml(summaryOnly),/本次未发现符合条件的线索/,'summary mode cannot establish full-text negative findings');
assert.match(ui.collectionHtml({collectionSummary:{contentCoverage:{requested:'15',attempted:-1,complete:'<img>'}}}),/计划读取 未记录 · 已尝试 未记录/);

// Zero-object stages show persisted reasons in both actual rendering functions.
assert.equal(ui.stageView({status:'completed',completed:0,total:0}).label,'无执行对象');
assert.equal(ui.stageView({status:'completed',completed:0,total:0}).showCounts,false);
assert.equal(ui.stageView({status:'completed',completed:0,total:0}).reason,'记录为 0 个处理对象，未记录具体原因。');
assert.equal(ui.stageView({status:'completed',completed:2,total:2}).showCounts,true);
assert.equal(ui.quality({stageCoverage:{qa:{status:'skipped',reason:'没有候选需要质检。'}},leads:{leads:[]}}).detail.startsWith('没有候选需要质检。'),true);
const renderScope = {window:{TaskInsights:ui},STAGE_LABELS:{details:'笔记详情'},esc:value => String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'),fmt:String,systemText:String,DisplayText:{message:String},technicalDetails:()=>'',statusLabel:String,matchingTaskSnapshot:()=>null};
vm.createContext(renderScope);
require('./ui-libraries.cjs').loadUiLibraries(renderScope, path.resolve(__dirname, '../assets'));
for (const [file,start,end] of [['workbench.js','function stageCoverageHtml','function reviewLabel'],['harness.html','function renderHarnessStageCoverage','function renderTaskSummary(data)']]) {
  const source = fs.readFileSync(path.join(__dirname,'../assets',file),'utf8');
  vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start))),renderScope);
}
const skippedCoverage = {stageCoverage:{details:{status:'skipped',completed:0,total:0,unit:'note',reason:'没有待读取笔记 <img>'}}};
for (const renderer of [renderScope.stageCoverageHtml,renderScope.renderHarnessStageCoverage]) {
  const rendered = renderer(skippedCoverage);
  assert.match(rendered,/笔记详情：未执行/); assert.match(rendered,/没有待读取笔记 &lt;img&gt;/);
  assert.doesNotMatch(rendered,/0 \/ 0|<img>/);
  const historicStage = renderer({stageCoverage:{details:{status:'completed',completed:0,total:0}}});
  assert.match(historicStage,/笔记详情：无执行对象/); assert.match(historicStage,/未记录具体原因/);
  assert.doesNotMatch(historicStage,/笔记详情：已完成|只有标题/);
}
console.log('PASS: independent task conclusions, content verification and gaps, zero-object stage reasons, truthful historical diagnostics.');
