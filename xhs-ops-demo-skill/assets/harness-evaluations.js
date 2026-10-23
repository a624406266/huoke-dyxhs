/* Fixed-sample evaluation: explicit review and administrator promotion. */
'use strict';
let evaluationRuns = [];
let currentEvaluationResult = null;
let evaluationDatasets = [];
let evaluationSamples = new Map();
let evaluationReadVersion = 0;
let promotionBusy = false;
const evaluationReviewed = new Set();
const evaluationQualityReviews = new Map();
function isSpecialisedEvaluation(result) { return ['critic_qa_agent','report_agent'].includes(result?.agentKey) || ['qa','report'].includes(result?.evaluationKind); }
function evaluationEligibleForPromotion(run) { return run?.passed === true && run?.humanReviewComplete === true && run?.split === 'acceptance' && (!isSpecialisedEvaluation(run) || run.qualityReviewComplete === true); }
function evaluationKindLabel(result) { return {qa:'质检专用评测',report:'报告专用评测',classification:'需求分类评测'}[result?.evaluationKind] || '需求分类评测'; }
const EVAL_CLASS = { demand:'明确需求', uncertain:'待确认', information:'学习讨论', promotion:'供方宣传', irrelevant:'无关' };
function evaluationState(run) { return run?.passed === true ? '最终通过' : run?.humanReviewComplete === true ? '验收未通过' : run?.autoPassed === true ? '待人工核验' : '未通过'; }
function evalSourceUrl(value) { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } }
async function loadEvaluationData() {
  if (!adminReady) return;
  try {
    const [data, results] = await Promise.all([get('/api/evals/datasets'), get('/api/evals/results')]);
    evaluationDatasets = data.datasets || [];
    const selected = $('evalDataset').value;
    $('evalDataset').innerHTML = '<option value="">选择样本集</option>' + evaluationDatasets.map(item => `<option value="${esc(item.id || item.datasetId)}">${esc(item.name || item.id || item.datasetId)} · ${fmt(item.sampleCount || item.count || 0)} 条</option>`).join('');
    if (selected) $('evalDataset').value = selected;
    evaluationRuns = results.runs || [];
    const selectedRun = $('evalRunSelect').value;
    $('evalRunSelect').innerHTML = '<option value="">选择评测记录</option>' + evaluationRuns.map(run => `<option value="${esc(run.runId || run.id)}">${esc(evaluationState(run))} · ${formatTime(run.createdAt)} · 提示词版本 ${esc(run.promptId || '当前版本')} · ${esc(String(run.runId || run.id).slice(0, 8))}</option>`).join('');
    if (selectedRun) $('evalRunSelect').value = selectedRun;
    if (typeof syncEvaluationPromptOptions === 'function') syncEvaluationPromptOptions();
    if (typeof refreshCandidateProof === 'function') refreshCandidateProof();
    if (typeof renderTaskInsights === 'function') renderTaskInsights();
    if (!evaluationDatasets.length) $('evalStatus').textContent = '尚无标注样本集。导入真实脱敏样本后才能进行质量验收。';
  } catch (error) { $('evalStatus').textContent = `读取评测记录失败：${DisplayText.message(error.message)}`; }
}
async function importEvaluationDataset() {
  const button = $('importEvaluationBtn'); if (button?.disabled) return;
  const file = $('evalFile').files?.[0]; if (!file) return adminNotice('请选择标注集 JSON 文件。');
  if (button) { button.disabled = true; button.textContent = '正在导入…'; }
  try {
    const data = JSON.parse(await file.text());
    if (!data.name || !Array.isArray(data.samples) || !data.samples.length) throw new Error('样本集需要填写名称，并至少包含一条样本；请按格式说明检查文件。');
    $('evalStatus').textContent = '正在校验并导入样本集…';
    const result = await post('/api/evals/import', data); await loadEvaluationData();
    $('evalDataset').value = result.datasetId; $('evalStatus').textContent = `已导入 ${data.name}，共 ${fmt(result.count || data.samples.length)} 条，尚未执行评测。`;
  } catch (error) { $('evalStatus').textContent = `导入失败：${DisplayText.message(error.message)}`; }
  finally { if (button) { button.disabled = false; button.textContent = '导入样本集'; } }
}
async function runEval() {
  setActivePage('evaluations');
  const datasetId = $('evalDataset').value; if (!datasetId) { $('evalDataset').focus(); return adminNotice('请先选择标注样本集。'); }
  const button = $('runEvaluationBtn'); if (button.disabled) return;
  const promptId = $('evalPrompt').value.trim();
  if (promptId && (!Number.isSafeInteger(Number(promptId)) || Number(promptId) < 1)) return adminNotice('提示词版本编号必须为正整数。');
  button.disabled = true; button.textContent = '评测运行中…'; $('evalStatus').textContent = '正在逐条实际调用模型；不会自动启用候选。请保留此页面。';
  try {
    const result = await post('/api/evals/run', { datasetId, ...(promptId ? { promptId: Number(promptId) } : {agentKey:$('evalAgentKey')?.value || 'lead_signal_agent'}), split: $('evalSplit').value });
    await loadEvaluationData();
    await displayEvaluationResult(result);
    $('evalRunSelect').value = result.runId;
    $('evalStatus').textContent = `本次评测：${evaluationState(result)}。请逐条核验输出与来源。`;
  } catch (error) { $('evalStatus').textContent = `评测失败：${DisplayText.message(error.message)}。未生成通过结论。`; }
  finally { button.disabled = false; button.textContent = '执行真实样本评测'; }
}
async function promotePrompt(promptId) {
  if (promotionBusy) return;
  const evalRunId = $('promoteEvalRun')?.value;
  if (!evalRunId) return adminNotice('请在此候选详情内选择已通过的冻结验收记录。');
  const run = evaluationRuns.find(r => String(r.runId || r.id) === evalRunId);
  if (!run || String(run.promptId || run.prompt_id) !== String(promptId) || !evaluationEligibleForPromotion(run)) return adminNotice('所选记录必须属于此候选，且已通过冻结验收与人工核验。', 'error');
  promotionBusy = true;
  const button = $('candidatePromoteBtn'); if (button) button.disabled = true;
  try {
    if (typeof adminMutationAllowed === 'function' && !await adminMutationAllowed()) return;
    if (!await WorkspaceUI.confirm({ title:'启用候选版本', message:`确认依据评测 ${evalRunId.slice(0, 8)} 启用提示词版本 ${promptId}？后续新任务将使用该版本。后端会再次校验版本与完整验收记录。`, confirmText:'确认启用' })) return;
    if (button?.isConnected) button.textContent = '正在启用…';
    await post('/api/prompts/promote', { promptId, evalRunId });
    await loadPrompts();
    if (currentTaskId) await loadTask(currentTaskId);
    if (typeof openCandidateDetail === 'function' && (typeof adminSelectedPromptId === 'undefined' || adminSelectedPromptId === String(promptId))) openCandidateDetail(promptId, false);
    adminNotice('候选已启用，新任务将使用该版本。', 'success');
  } catch (error) { adminNotice(`未启用：${DisplayText.message(error.message)}`, 'error'); }
  finally {
    promotionBusy = false;
    if (button?.isConnected) { button.disabled = false; button.textContent = '确认启用此版本'; }
    if (typeof refreshCandidateProof === 'function') refreshCandidateProof();
  }
}
function evalMetric(value) { return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '未记录'; }
function renderEvaluationSummary(result) {
  const m = result.metrics || {}, rows = result.rows || [];
  const metricHtml = isSpecialisedEvaluation(result)
    ? `<div><b>${fmt(m.machineChecksPassed)}</b><span>固定约束通过</span></div><div><b>${fmt(m.failedSamples)}</b><span>调用失败</span></div><div><b>${fmt(m.manualQualityReviewRequired || rows.length)}</b><span>需逐条质量核验</span></div>`
    : `<div><b>${evalMetric(m.relevance?.precision)}</b><span>相关性精确率</span></div><div><b>${evalMetric(m.relevance?.recall)}</b><span>相关性召回率</span></div><div><b>${evalMetric(m.demand?.precision)}</b><span>需求精确率</span></div><div><b>${evalMetric(m.demand?.recall)}</b><span>需求召回率</span></div>`;
  const requirements = { atLeast100Samples:'总样本 ≥ 100', development40:'调试集 ≥ 40', acceptance60:'验收集 ≥ 60', atLeast3Topics:'覆盖 ≥ 3 个主题', sourcesHumanVerified:'来源已人工确认', acceptanceRun:'冻结验收集' };
  $('evalResultSummary').innerHTML = `<div class="admin-section-heading"><h3>${esc(evaluationState(result))}</h3><span class="pill ${result.passed ? 'pass' : 'pending'}">${esc(evaluationKindLabel(result))} · ${result.split === 'development' ? '调试集' : '冻结验收集'} · ${fmt(rows.length)} 条</span></div><div class="admin-metric-row">${metricHtml}</div><div class="audit-meta">${Object.entries(result.requirements || {}).map(([key, passed]) => `<span class="pill ${passed ? 'pass' : 'pending'}">${passed ? '✓' : '○'} ${esc(requirements[key] || DisplayText.code(key, '其他验收要求'))}</span>`).join('')}</div><p class="small">本次人工核验要求：${esc((result.requiredHumanChecks || ['引用与原文一致','无无依据的身份、预算或承诺']).map(DisplayText.message).join('；'))}</p>${result.humanReviewComplete ? `<p class="small">人工核验已记录：${esc(result.humanReview?.note || '已完成')}。</p>` : '<p class="small">自动指标不代表最终通过。原文和模型输出需逐条人工核验。</p>'}`;
}
function updateEvaluationReviewCount() {
  const rows = currentEvaluationResult?.rows || [];
  if ($('evalReviewCount')) $('evalReviewCount').textContent = `已逐条核对 ${evaluationReviewed.size} / ${rows.length}`;
}
function markEvaluationRow(index, checked) {
  const row = currentEvaluationResult?.rows?.[index]; if (!row) return;
  checked ? evaluationReviewed.add(String(row.id)) : evaluationReviewed.delete(String(row.id));
  updateEvaluationReviewCount();
}
function setEvaluationQuality(index, field, value) {
  const row = currentEvaluationResult?.rows?.[index]; if (!row || !['decision','note'].includes(field)) return;
  const prior = evaluationQualityReviews.get(String(row.id)) || {};
  evaluationQualityReviews.set(String(row.id), {...prior,[field]:String(value)});
}
function evaluationQualityHtml(row, index) {
  if (!isSpecialisedEvaluation(currentEvaluationResult)) return '';
  const review = evaluationQualityReviews.get(String(row.id)) || {};
  return `<div class="admin-eval-quality"><strong>本条人工质量结论</strong><p class="small">依据完整冻结输入与预期结果判断，不能用自动文本匹配代替。</p><label for="evalQualityDecision-${index}">结论（必选）</label><select id="evalQualityDecision-${index}" onchange="setEvaluationQuality(${index},'decision',this.value)"><option value="">请选择</option><option value="pass" ${review.decision === 'pass' ? 'selected' : ''}>通过</option><option value="fail" ${review.decision === 'fail' ? 'selected' : ''}>未通过</option></select><label for="evalQualityNote-${index}">本条核验依据（必填）</label><textarea id="evalQualityNote-${index}" oninput="setEvaluationQuality(${index},'note',this.value)" placeholder="写明原文支持、问题归因或报告事实覆盖的核验结果">${esc(review.note || '')}</textarea></div>`;
}
function evaluationRowLabel(row, result) {
  if (row.error) return '调用失败';
  if (result.agentKey === 'critic_qa_agent') return row.prediction?.approved === true ? '质检判断通过' : row.prediction?.approved === false ? '质检要求复核' : '质检输出';
  if (result.agentKey === 'report_agent') return '报告输出';
  return EVAL_CLASS[row.classification] || '未判断';
}
function evaluationRowSummary(row, result) {
  const prediction = row.prediction || {};
  const summary = prediction.demand_summary || prediction.executive_summary || prediction.reason || prediction.summary;
  if (summary) return DisplayText.report(DisplayText.humanize(summary));
  if (result.agentKey === 'critic_qa_agent') return `记录 ${prediction.issues?.length || 0} 个质量问题、${prediction.review_requests?.length || 0} 项复核要求；请展开完整输出逐项核对。`;
  if (result.agentKey === 'report_agent') return '请展开完整报告，核对统计、事实、限制与建议。';
  return '请核对分类和下方引用证据。';
}
function renderEvaluationRows(result, samples) {
  const rows = result.rows || [], originals = new Map(samples.map(s => [String(s.id), s]));
  $('evalComparison').innerHTML = `<div class="admin-section-heading"><h3>逐条核验</h3><span id="evalReviewCount" class="small"></span></div>` + (rows.length ? rows.map((row, index) => {
    const sample = originals.get(String(row.id)), url = evalSourceUrl(row.sourceUrl || sample?.sourceUrl), prediction = row.prediction || {};
    const evidence = Array.isArray(row.evidence) ? row.evidence : row.evidence ? [row.evidence] : [];
    return `<article class="admin-eval-row"><header><strong>${index + 1}. ${esc(row.id)}</strong><label class="checks"><input type="checkbox" ${evaluationReviewed.has(String(row.id)) ? 'checked' : ''} onchange="markEvaluationRow(${index},this.checked)" />已核对本条</label></header><div class="admin-compare-grid"><section><h4>来源原文 ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开来源 ↗</a>` : ''}</h4><div class="admin-readable">${(row.inputText || sample?.text) ? esc(row.inputText || sample.text) : '<span class="small">当前记录未提供可对照原文。请打开来源核实；缺少依据时不要勾选通过。</span>'}</div><p class="small">人工标签：${row.labels?.relevant === true ? '相关' : row.labels?.relevant === false ? '无关' : '未记录'} / ${esc(EVAL_CLASS[row.labels?.classification] || '未记录')}</p>${isSpecialisedEvaluation(result) ? `<details class="admin-raw"><summary>完整冻结输入与人工预期</summary><pre>${esc(JSON.stringify({input:row.inputContext || sample?.input || {},expected:row.expected || sample?.expected || {}},null,2))}</pre></details>` : ''}</section><section><h4>模型输出 <span class="pill ${row.error ? 'drop' : 'pending'}">${esc(evaluationRowLabel(row,result))}</span></h4>${row.error ? `<p class="admin-error">${esc(DisplayText.message(row.error))}</p>` : ''}<div class="admin-readable">${esc(evaluationRowSummary(row,result))}</div><h4>${isSpecialisedEvaluation(result) ? '引用与事实核验' : '引用证据'}</h4>${evidence.length ? evidence.map(e => `<blockquote>${esc(typeof e === 'string' ? e : JSON.stringify(e))}</blockquote>`).join('') : isSpecialisedEvaluation(result) ? '<p class="small">请对照完整冻结输入，逐项核对输出结论的原文依据。</p>' : '<p class="small">没有已保存的引用证据。</p>'}<p class="small">${isSpecialisedEvaluation(result) ? `固定约束：${row.machineChecksPassed ? '通过' : '未通过'}；引用与事实仍须人工核验。` : `自动检查：引用 ${row.citationPassed ? '通过' : '未通过'} · 虚构信息 ${row.fabricationPassed ? '通过' : '未通过'}`}</p><details class="admin-raw"><summary>完整模型输出</summary><pre>${esc(JSON.stringify(prediction, null, 2))}</pre></details></section></div>${evaluationQualityHtml(row,index)}</article>`;
  }).join('') : '<div class="workspace-empty">此记录没有逐条输出，不能完成人工核验。</div>');
  updateEvaluationReviewCount();
}
async function displayEvaluationResult(result) {
  const changed = currentEvaluationResult?.runId !== result.runId;
  currentEvaluationResult = result;
  if ($('evalReviewPanel')) $('evalReviewPanel').disabled = !Array.isArray(result.rows) || !result.rows.length;
  $('evalOutput').textContent = JSON.stringify(result, null, 2);
  if (changed) {
    evaluationReviewed.clear(); evaluationQualityReviews.clear();
    for (const review of result.qualityReviews || []) evaluationQualityReviews.set(String(review.sampleId), {decision:review.passed === true ? 'pass' : 'fail',note:review.note || ''});
    for (const id of ['evalReviewedAll', 'evalNoFabrication', 'evalCitationsVerified']) $(id).checked = false;
    $('evalHumanNote').value = '';
  }
  renderEvaluationSummary(result);
  const generation = ++evaluationReadVersion;
  const datasetId = result.datasetId;
  $('evalComparison').innerHTML = '<div class="workspace-empty">正在读取原文…</div>';
  try {
    if (!evaluationSamples.has(datasetId)) {
      const data = await get(`/api/evals/datasets?datasetId=${encodeURIComponent(datasetId || '')}`);
      evaluationSamples.set(datasetId, data.samples || []);
    }
    if (generation !== evaluationReadVersion || currentEvaluationResult?.runId !== result.runId) return;
    renderEvaluationRows(result, evaluationSamples.get(datasetId) || []);
  } catch (error) {
    if (generation !== evaluationReadVersion) return;
    renderEvaluationRows(result, []);
    $('evalStatus').textContent = `原文读取失败：${DisplayText.message(error.message)}。请核实来源后再记录人工核验。`;
  }
}
async function selectEvaluationRun() {
  const result = evaluationRuns.find(run => String(run.runId || run.id) === $('evalRunSelect').value);
  if (result) await displayEvaluationResult(result);
  else { ++evaluationReadVersion; currentEvaluationResult = null; evaluationReviewed.clear(); evaluationQualityReviews.clear(); if ($('evalReviewPanel')) $('evalReviewPanel').disabled = true; for (const id of ['evalReviewedAll','evalNoFabrication','evalCitationsVerified']) $(id).checked = false; $('evalHumanNote').value = ''; $('evalOutput').textContent = ''; $('evalResultSummary').innerHTML = ''; $('evalComparison').innerHTML = '<div class="workspace-empty">选择评测记录后，对照原文核验每条输出。</div>'; }
}
async function confirmEvaluationReview() {
  const result = currentEvaluationResult, button = $('reviewEvaluationBtn'); if (button.disabled) return;
  if (!result?.runId || !Array.isArray(result.rows) || !result.rows.length) return adminNotice('请先选择有逐条结果的真实评测记录。');
  if (result.rows.some(row => !evaluationReviewed.has(String(row.id)))) return adminNotice('请在逐条对照区核对并勾选全部样本。');
  if (!['evalReviewedAll', 'evalNoFabrication', 'evalCitationsVerified'].every(id => $(id).checked)) return adminNotice('请实际逐条核对后，明确勾选全部三项核验声明。');
  const note = $('evalHumanNote').value.trim(); if (!note) { $('evalHumanNote').focus(); return adminNotice('请填写人工核验说明。'); }
  const qualityReviews = isSpecialisedEvaluation(result) ? result.rows.map(row => {
    const review = evaluationQualityReviews.get(String(row.id)) || {};
    return {sampleId:row.id,passed:review.decision === 'pass',note:(review.note || '').trim(),decision:review.decision};
  }) : [];
  if (qualityReviews.some(review => !['pass','fail'].includes(review.decision) || !review.note)) return adminNotice('专用评测需为每条样本选择质量结论并填写独立核验依据。');
  const reviewedSampleIds = result.rows.map(row => row.id);
  if (reviewedSampleIds.some(id => !id)) return adminNotice('评测输出缺少样本编号，不能记录完整人工核验。');
  if (!await WorkspaceUI.confirm({ title:'保存人工核验', message:`确认已核对评测 ${result.runId.slice(0, 8)} 的全部 ${reviewedSampleIds.length} 条输出？将记录当前管理员及核验说明。`, confirmText:'确认记录' })) return;
  button.disabled = true; button.textContent = '正在保存…';
  try {
    const reviewed = await post('/api/evals/review', { runId: result.runId, reviewedSampleIds, note, noFabrication: true, citationsVerified: true, ...(qualityReviews.length ? {qualityReviews:qualityReviews.map(({decision,...review}) => review)} : {}) });
    await loadEvaluationData();
    const updated = evaluationRuns.find(run => run.runId === result.runId) || reviewed;
    // A completed request must not replace another record the user opened meanwhile.
    if (currentEvaluationResult?.runId === result.runId) { await displayEvaluationResult(updated); $('evalRunSelect').value = result.runId; }
    $('evalStatus').textContent = updated.passed === true ? '人工核验已记录，评测通过。可在版本详情中选择此记录手动启用。' : '人工核验已记录；自动指标或验收条件仍未通过，不能启用候选。';
  } catch (error) { $('evalStatus').textContent = `人工核验未保存：${DisplayText.message(error.message)}`; }
  finally { button.disabled = false; button.textContent = '保存人工核验'; }
}
