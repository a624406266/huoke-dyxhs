/* Read-only task summaries. Execution, model QA and human decisions never imply each other. */
(function (scope) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const positiveId = value => Number.isSafeInteger(Number(value)) && Number(value) > 0;
  function leadsOf(data) { return data?.leads?.leads || data?.result?.leads?.leads || []; }
  function contentCoverage(data) {
    const value = data?.collectionSummary?.contentCoverage ?? data?.result?.collectionSummary?.contentCoverage;
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  }
  function hasContentGaps(coverage) {
    return coverage && (coverage.mode === 'summary' || ['missing','failed','pending'].some(key => Number.isSafeInteger(coverage[key]) && coverage[key] > 0) || (Array.isArray(coverage.gaps) && coverage.gaps.length > 0));
  }
  function stageView(row = {}) {
    const labels = {pending:'待处理',running:'处理中',completed:'已完成',partial:'部分完成',failed:'失败',skipped:'未执行'};
    const counts = Number.isInteger(row.completed) && row.completed >= 0 && Number.isInteger(row.total) && row.total >= 0;
    const empty = row.status === 'completed' && counts && row.total === 0 && row.completed === 0;
    const reason = typeof row.reason === 'string' && row.reason.trim() ? row.reason : empty ? '记录为 0 个处理对象，未记录具体原因。' : row.status === 'skipped' ? '未记录该阶段未执行的原因。' : '';
    return {label:empty ? '无执行对象' : labels[row.status] || '完成情况未记录',showCounts:counts && !empty && row.status !== 'skipped',reason};
  }
  function execution(data = {}) {
    const status = data.status || data.result?.status;
    const terminal = ['completed','partial','failed','stopped','interrupted','needs_action'].includes(status);
    const label = {completed:'执行已完成',partial:'流程已结束 · 执行部分完成',failed:'执行失败',stopped:'执行已停止',running:'执行中',interrupted:'执行已中断',needs_action:'执行待处理'}[status] || '执行状态未记录';
    if (status !== 'partial') return {label,terminal,detail:'',progressText:status === 'completed' ? '流程已结束' : label};
    const coverage = data.stageCoverage || data.result?.stageCoverage || {};
    const summary = data.collectionSummary || data.result?.collectionSummary || {};
    const labels = {search:'搜索采集',relevance:'相关性判断',details:'正文读取',comments:'评论读取',lead_signal:'需求研判',qa:'模型质检',report:'报告生成'};
    const gaps = [], covered = new Set();
    for (const [stage,name] of Object.entries(labels)) {
      const row = coverage[stage] || {};
      if (!['partial','failed','pending','running'].includes(row.status)) continue;
      const counts = [row.completed,row.total].every(value => Number.isSafeInteger(value) && value >= 0);
      if (counts && row.total > row.completed) {
        const unit = {query:'项',note:'条',candidate:'条',report:'项'}[row.unit] || '项';
        gaps.push(`${name}有 ${row.total - row.completed} ${unit}未完成（${row.completed}/${row.total}）`);
      } else if (['partial','failed'].includes(row.status)) gaps.push(`${name}仍有未解决异常`);
      else continue;
      covered.add(stage);
    }
    const failures = new Map();
    for (const failure of Array.isArray(summary.failures) ? summary.failures : []) {
      if (!failure || typeof failure !== 'object') continue;
      const stage = failure.stage === 'read_detail' ? 'details' : failure.stage;
      if (!covered.has(stage)) failures.set(stage,(failures.get(stage) || 0) + 1);
    }
    for (const [stage,count] of failures) gaps.push(`${labels[stage] || (stage === 'time_filter' ? '发布时间核验' : '执行过程')}记录 ${count} 项异常`);
    const reason = gaps.join('；') || '存在读取、证据或分析缺口，请查看阶段明细';
    return {label,terminal,detail:`${reason}。可查看已有结果，未完成项需补处理。`,progressText:'流程已结束 · 部分结果待补处理'};
  }
  function quality(data) {
    const leads = leadsOf(data), qa = data?.qa || data?.result?.qa || {}, coverage = data?.stageCoverage?.qa || data?.result?.stageCoverage?.qa;
    const counts = {approved:0,needs_review:0,rejected:0,pending:0};
    leads.forEach(lead => { const status = Object.hasOwn(counts, lead.qa_status) ? lead.qa_status : 'pending'; counts[status]++; });
    const detail = `通过 ${counts.approved} · 待复核 ${counts.needs_review} · 未通过 ${counts.rejected} · 未检查 ${counts.pending}`;
    if (coverage && coverage.status !== 'completed') return {label: coverage.status === 'skipped' ? '质检未执行' : '质检尚未完成', detail:coverage.reason ? `${coverage.reason} ${detail}` : detail, tone:'pending'};
    if (!leads.length) return {label:coverage?.status === 'completed' ? '无候选待质检' : '质检未记录',detail:'没有可核对的候选质检结果',tone:'pending'};
    if (counts.pending) return {label:'质检尚未完成', detail, tone:'pending'};
    if (qa.approved === false || counts.rejected || counts.needs_review) return {label:'质检需复核',detail,tone:'pending'};
    return {label:counts.approved === leads.length ? '模型质检 通过' : '质检未记录',detail,tone:counts.approved === leads.length ? 'pass' : 'pending'};
  }
  function human(data) {
    const leads = leadsOf(data), counts = {approved:0,rejected:0,pending:0};
    leads.forEach(lead => { const status = lead.review?.status; counts[Object.hasOwn(counts,status) ? status : 'pending']++; });
    return {label: !leads.length ? '暂无待审核候选' : counts.pending ? `${counts.pending} 条待人工审核` : '人工审核已记录', detail:`通过 ${counts.approved} · 驳回 ${counts.rejected} · 待审核 ${counts.pending} · 含业务复核提示 ${leads.filter(lead => lead.business_review_required === true).length}`, tone:leads.length && !counts.pending ? 'pass' : 'pending'};
  }
  function evolution(data, prompts = [], runs = []) {
    const record = data?.evolution || data?.result?.evolution || {}, taskId = data?.taskId || data?.id;
    const ids = new Set((record.candidatePromptIds || []).filter(positiveId).map(String));
    prompts.filter(prompt => taskId && String(prompt.sourceTaskId) === String(taskId) && positiveId(prompt.id)).forEach(prompt => ids.add(String(prompt.id)));
    const candidates = [...ids].map(id => {
      const prompt = prompts.find(item => String(item.id) === id), supplied = (record.candidateStates || []).find(item => String(item.promptId) === id);
      const matched = runs.filter(run => String(run.promptId || run.prompt_id) === id && run.split === 'acceptance');
      let stage = supplied?.evaluationState || 'not_evaluated';
      // The service checks prompt hashes, service scope and current policy. A
      // stale UI proof must not override that authoritative candidate state.
      if (!supplied?.evaluationState) {
        if (matched.some(run => run.passed === true && run.humanReviewComplete === true)) stage = 'passed';
        else if (matched.some(run => run.autoPassed === true && run.humanReviewComplete !== true)) stage = 'pending_human_review';
        else if (matched.length) stage = 'failed';
      }
      const status = supplied?.status || prompt?.status;
      return {promptId:Number(id), status, stage, label:status === 'active' ? '已启用' : status === 'disabled' ? '已禁用' : stage === 'passed' ? '待启用' : stage === 'pending_human_review' ? '待人工验收' : stage === 'failed' ? '评测未通过' : '待评测'};
    });
    if (['running','pending'].includes(record.status) || data?.isEvolving) return {label:'正在复盘',detail:'运行结束后的独立改进流程；候选不会自动启用。',tone:'pending',candidates,record};
    if (['failed','interrupted'].includes(record.status)) return {label:record.status === 'interrupted' ? '复盘已中断' : '复盘失败',detail:(scope.DisplayText ? scope.DisplayText.message(record.error) : record.error) || '未完成复盘，需明确确认重试；不会自动重发。',tone:'drop',candidates,record};
    if (candidates.length) {
      const active = candidates.filter(item => item.status === 'active').length;
      const label = active === candidates.length ? '已启用' : active ? '部分已启用' : candidates.some(item => item.stage === 'pending_human_review') ? '待人工验收' : candidates.some(item => item.stage === 'passed') ? '待启用' : candidates.every(item => item.status === 'disabled') ? '候选已禁用' : candidates.every(item => item.stage === 'failed') ? '评测未通过' : '建议候选 · 待评测';
      return {label,detail:`${candidates.length} 个关联候选 · 已启用 ${active} 个；质量收益需后续真实运行验证。`,tone:active === candidates.length ? 'pass' : 'pending',candidates,record};
    }
    return {label:record.status === 'no_changes' ? '已复盘 · 无新候选' : ['candidates_ready','awaiting_evaluation'].includes(record.status) ? '建议候选 · 待评测' : record.status === 'needs_review' ? '复盘建议待确认' : '未复盘',detail:record.status === 'no_changes' ? '本次没有产生新的版本候选。' : '未记录可核验的候选与启用结果。',tone:'pending',candidates,record};
  }
  function resultConclusion(data) {
    const coverage = contentCoverage(data), leads = data?.leads?.leads ?? data?.result?.leads?.leads;
    const excluded = coverage && Object.hasOwn(coverage,'excluded') ? coverage.excluded : 0;
    const verified = coverage?.mode === 'full_text' && ['requested','complete','missing','failed','pending'].every(key => Number.isSafeInteger(coverage[key]) && coverage[key] >= 0) && Number.isSafeInteger(excluded) && excluded >= 0 && coverage.complete + excluded === coverage.requested && !hasContentGaps(coverage);
    return hasContentGaps(coverage) ? '正文核验仍有缺口，不能据此认定没有需求。' : data?.status === 'completed' && verified && Array.isArray(leads) && !leads.length ? '本次未发现符合条件的线索。' : '';
  }
  function summaryHtml(data, prompts = [], runs = []) {
    const executionState = execution(data), coverage = contentCoverage(data), resultDetail = resultConclusion(data);
    const blocks = [{title:'任务执行',label:executionState.label,detail:executionState.detail + resultDetail + '候选的业务复核和人工审核单独记录。',tone:data?.status === 'completed' && !hasContentGaps(coverage) ? 'pass' : 'pending'}, {title:'模型质检',...quality(data)}, {title:'人工审核',...human(data)}, {title:'版本改进',...evolution(data,prompts,runs)}];
    return blocks.map(block => `<section class="task-insight"><span class="task-insight-title">${block.title}</span><strong class="task-insight-${block.tone}">${escape(block.label)}</strong><p>${escape(block.detail)}</p></section>`).join('');
  }
  const businessStages = {search:'搜索内容',details:'读取内容',relevance:'分析相关内容',comments:'读取评论',lead_signal:'识别需求',qa:'核对结果',report:'整理报告'};
  function businessStageLabel(stage) { return businessStages[stage] || '处理任务'; }
  function workbenchStatus(data = {}) {
    const status = data.status || data.result?.status, coverage = contentCoverage(data);
    const label = {completed:hasContentGaps(coverage) ? '部分结果可用' : '任务已完成',partial:'部分结果可用',failed:'任务未完成',stopped:'任务已停止',running:'正在处理',interrupted:'任务已中断',needs_action:'需要处理'}[status] || '任务状态待确认';
    const stages = data.stageCoverage || data.result?.stageCoverage || {};
    const gaps = Object.entries(businessStages).flatMap(([key,name]) => {
      const row = stages[key];
      if (!row || !['partial','failed','pending','running'].includes(row.status)) return [];
      if (Number.isSafeInteger(row.total) && Number.isSafeInteger(row.completed) && row.completed >= 0 && row.total > row.completed) {
        return [`${name}：${row.total - row.completed} ${row.unit === 'query' || row.unit === 'report' ? '项' : '条'}尚未完成`];
      }
      return ['partial','failed'].includes(row.status) ? [`${name}尚未完成`] : [];
    });
    const conclusion = resultConclusion({...data,status});
    const detail = status === 'running' ? '结果会陆续更新，请等待分析结束后再核对线索。'
      : status === 'completed' && !hasContentGaps(coverage) ? conclusion || (leadsOf(data).length ? '请对照原文审核线索，再导出或跟进。' : '暂无可审核线索；结果完整性尚未核实，不能据此认定没有需求。')
      : `${gaps.length ? gaps[0] + '。' : ''}${conclusion || '已有结果仍可查看，尚不能据此得出完整结论。'}${['failed','interrupted','needs_action'].includes(status) ? '请检查平台登录状态，或联系维护人员处理。' : ''}`;
    return {label,detail,gaps,tone:status === 'completed' && !hasContentGaps(coverage) ? 'complete' : status === 'running' ? 'running' : 'attention'};
  }
  function workbenchSummaryHtml(data) {
    const state = workbenchStatus(data);
    return `<div class="wb-task-summary wb-task-summary-${state.tone}"><strong>${escape(state.label)}</strong><p>${escape(state.detail)}</p></div>`;
  }
  function workbenchDetailsHtml(data = {}) {
    const summary = data.collectionSummary || data.result?.collectionSummary || {}, content = contentCoverage(data), state = workbenchStatus(data);
    const number = value => Number.isSafeInteger(value) && value >= 0;
    const facts = [];
    if (number(summary.notesCollected)) facts.push(`已采集 ${summary.notesCollected} 篇笔记`);
    if (number(summary.commentsCollected)) facts.push(`已读取 ${summary.commentsCollected} 条评论`);
    if (content && number(content.complete) && number(content.requested)) facts.push(`正文完整 ${content.complete} / ${content.requested} 篇`);
    if (content && number(content.excluded) && content.excluded > 0) facts.push(`已核实排除 ${content.excluded} 篇不符合采集范围的内容`);
    const notices = [...state.gaps];
    if (hasContentGaps(content)) notices.push('正文尚未完整核验，可能遗漏需求；请结合原文审核，必要时联系维护人员补全。');
    else if (!content) notices.push('本任务未记录正文完整性，不能仅凭任务结束或零结果认定没有需求。');
    if (number(summary.filterCounts?.unknown_publish_time) && summary.filterCounts.unknown_publish_time > 0) notices.push(`${summary.filterCounts.unknown_publish_time} 条内容的发布时间待核验，尚不能确认符合所选时间范围。`);
    if (number(summary.filterCounts?.outside_time_range) && summary.filterCounts.outside_time_range > 0) notices.push(`${summary.filterCounts.outside_time_range} 条内容超出所选时间范围。`);
    return `<div class="wb-task-explanation">${facts.length ? `<p>${facts.map(escape).join(' · ')}</p>` : ''}${notices.length ? `<ul>${notices.map(text => `<li>${escape(text)}</li>`).join('')}</ul>` : '<p>本次结果仅覆盖已采集的内容；需求是否有效仍需逐条核对。</p>'}</div>`;
  }
  function inquiry(lead) {
    const pending = lead?.classification === 'uncertain' || (lead?.classification === 'demand' && (lead?.qualified !== true || (lead.qa_status && lead.qa_status !== 'approved')));
    const priority = pending && ['high','medium','low'].includes(lead.review_priority) ? lead.review_priority : 'none';
    const type = {price_inquiry:'待确认询价',service_interest:'待确认服务意向',insufficient_context:'语境不足'}[lead?.inquiry_type];
    const missing = Array.isArray(lead?.missing_information) ? lead.missing_information.filter(item => typeof item === 'string' && item.trim()) : [];
    return {pending,priority,rank:{high:0,medium:1,low:2,none:3}[priority],label:type || (pending ? '待确认需求' : ''),priorityLabel:{high:'优先核验',medium:'常规核验',low:'较低优先级',none:'未设置核验优先级'}[priority],missing};
  }
  function inquiryHtml(lead) {
    const item = inquiry(lead); if (!item.pending && !item.missing.length && !lead?.business_review_required) return '';
    const requests = Array.isArray(lead?.qa_review_requests) ? lead.qa_review_requests : [];
    return `<div class="lead-block task-inquiry"><b>${escape(item.label || '业务信息待核验')}</b><div>${escape(item.priorityLabel)} · ${item.pending ? '尚未等同于合格委托' : '业务信息仍须核对，人工审核单独记录'}</div><div class="small"><b>缺失信息：</b>${escape(item.missing.length ? item.missing.join('、') : '未记录具体缺项，请核对原文语境。')}</div>${requests.length ? `<div class="small"><b>业务复核要求：</b>${requests.map(request => escape(typeof request === 'string' ? request : request.reason || request.message || request.question || (scope.DisplayText ? scope.DisplayText.humanize(request) : '请查看复核详情'))).join('；')}</div>` : ''}</div>`;
  }
  function collectionHtml(data = {}) {
    const result = data.result || {}, summary = {...(result.collectionSummary || {}),...(data.collectionSummary || {})};
    const filters = summary.filterCounts, resolution = summary.timeResolution;
    const hasFilters = filters && typeof filters === 'object' && !Array.isArray(filters) && Object.keys(filters).length > 0;
    const count = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : '未记录';
    const content = contentCoverage(data);
    const contentText = content
      ? `计划读取 ${count(content.requested)} · 已尝试 ${count(content.attempted)} · 已取得详情 ${count(content.fetched)} · 正文完整 ${count(content.complete)}${Object.hasOwn(content,'excluded') ? ` · 已核实排除 ${count(content.excluded)}` : ''} · 正文缺失 ${count(content.missing)} · 读取失败 ${count(content.failed)}${Object.hasOwn(content,'pending') ? ` · 尚未处理 ${count(content.pending)}` : ''}`
      : '未记录正文读取与完整性；不能根据任务状态或笔记总数认定已核验正文。';
    const modeText = content?.mode === 'summary' ? '本次使用摘要模式，正文未完整核验；不能据此认定没有需求。' : hasContentGaps(content) ? '正文核验仍有缺口；不能据此认定没有需求。' : '';
    const gaps = Array.isArray(content?.gaps) ? content.gaps.filter(item => item && typeof item === 'object') : [];
    const gapHtml = gaps.length ? `<details class="small"><summary>查看 ${gaps.length} 条正文核验缺口</summary><ul>${gaps.map(item => `<li>${escape(({xiaohongshu:'小红书',xhs:'小红书',douyin:'抖音'})[item.platform] || '平台未记录')} · 笔记 ${escape(item.noteId || '编号未记录')}：${escape(typeof item.reason === 'string' && item.reason.trim() ? item.reason : '缺口原因未记录')}</li>`).join('')}</ul></details>` : '';
    const contentHtml = `<p class="small"><b>正文读取与完整性：</b>${contentText}${modeText ? `<br>${escape(modeText)}` : ''}</p>${gapHtml}`;
    const labels = {invalid_record:'无效记录',non_note:'非笔记内容',missing_note_id:'缺少笔记编号',duplicate:'重复内容',excluded_keyword:'命中排除关键词',outside_time_range:'发布时间超出范围',unknown_publish_time:'发布时间仍待核验',capacity_limit:'达到采集上限'};
    const filtering = hasFilters
      ? Object.entries(labels).map(([key,label]) => `${label} <b>${count(filters[key])}</b>`).join('；')
      : data.isRunning || data.status === 'running' ? '尚未记录过滤明细；已有总数不能用于推断各类过滤原因。' : '旧任务未记录过滤明细；不能根据已扫描总数反推各类过滤原因。';
    function referenceTime(reference) {
      if (reference === undefined || reference === null || reference === '') return '未记录';
      const numeric = typeof reference === 'number' || (typeof reference === 'string' && /^\d+(\.\d+)?$/.test(reference)) ? Number(reference) : null;
      const date = new Date(numeric !== null ? (numeric < 1e11 ? numeric * 1000 : numeric) : reference);
      // The collection window is fixed to UTC+08:00, independently of the
      // workstation's local timezone. Preserve the stored instant when rendering.
      const beijing = new Date(date.valueOf() + 8 * 60 * 60 * 1000);
      return Number.isNaN(beijing.valueOf()) ? '格式无法识别' : beijing.toISOString().replace('T',' ').replace('Z',' 北京时间（UTC+08:00）');
    }
    const cumulative = summary.filterCountsScope === 'executed_rounds';
    const references = Array.isArray(summary.timeReferences) ? summary.timeReferences.filter(item => item && typeof item === 'object') : [];
    const referenceLabel = cumulative && references.length
      ? references.map(item => `第 ${count(item.round)} 轮：${referenceTime(item.timeReferenceAt)}`).join('；')
      : referenceTime(summary.timeReferenceAt ?? data.timeReferenceAt ?? result.timeReferenceAt);
    const resolutionText = resolution && typeof resolution === 'object' && !Array.isArray(resolution)
      ? `额外补读详情 ${count(resolution.attempted)} · 已确认时间 ${count(resolution.resolved)} · 仍待核验 ${count(resolution.unresolved)} · 补证失败 ${count(resolution.failed)}${Object.hasOwn(resolution,'interrupted') ? ` · 补证中断 ${count(resolution.interrupted)}` : ''}`
      : '未记录详情补证统计。';
    const unprocessedCount = count(summary.unprocessedNotes);
    const unprocessedText = Object.hasOwn(summary,'unprocessedNotes') ? `<p class="small"><b>已返回但尚未处理／纳入：</b>${unprocessedCount}${unprocessedCount === '未记录' ? '' : ' 条'}</p>` : '';
    const zeroReason = summary.zeroResultReason ?? data.zeroResultReason ?? result.zeroResultReason;
    const notes = data.notes ?? result.notes;
    const knownEmpty = summary.notesCollected === 0 || (Array.isArray(notes) && notes.length === 0);
    const zeroText = typeof zeroReason === 'string' && zeroReason.trim() ? zeroReason : knownEmpty && !data.isRunning && data.status !== 'running' ? '未记录无结果原因，不能据此认定没有需求。' : '';
    return `<div class="item task-collection-diagnostics"><b>采集过滤、正文与发布时间核验${cumulative ? '（按已执行轮次累计）' : ''}</b>${contentHtml}<p class="small"><b>过滤原因：</b>${filtering}</p>${unprocessedText}<p class="small"><b>详情补证：</b>${resolutionText}<br>仅统计为确认时间范围而额外补读的详情，不包含正常内容详情读取。</p><p class="small"><b>时间筛选基准（${cumulative ? '各轮分别冻结' : '本轮冻结'}）：</b>${escape(referenceLabel)}</p>${zeroText ? `<p class="small"><b>无结果说明：</b>${escape(zeroText)}</p>` : ''}<p class="small">发布时间待核验不等于没有需求；未知发布时间的内容不能认定为满足所选时间范围。</p></div>`;
  }
  scope.TaskInsights = {execution,quality,human,evolution,summaryHtml,inquiry,inquiryHtml,collectionHtml,stageView,workbenchStatus,workbenchSummaryHtml,workbenchDetailsHtml,businessStageLabel};
})(typeof window === 'undefined' ? globalThis : window);
