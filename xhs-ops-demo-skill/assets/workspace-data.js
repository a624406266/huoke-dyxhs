/* Shared transport and polling. Callers own business actions and confirmation. */
(function (scope) {
  'use strict';
  const stages = Object.freeze({ search: '搜索采集', details: '笔记详情', relevance: '相关性研判', comments: '评论读取', lead_signal: '需求识别', qa: '质量核验', report: '报告生成' });
  const coverageStatuses = Object.freeze({ pending: '待处理', running: '处理中', completed: '已完成', partial: '部分完成', failed: '失败', skipped: '未执行' });
  const coverageUnits = Object.freeze({ query: '关键词×平台组合', note: '篇笔记', candidate: '条候选', report: '份报告' });

  async function requestJson(url, options = {}) {
    const { fetcher = (...args) => fetch(...args), onUnauthorized, unauthorizedStatuses = [401], body, ...request } = options;
    const response = await fetcher(url, {
      credentials: 'same-origin', ...request,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json', ...request.headers }, body: JSON.stringify(body) })
    });
    if (onUnauthorized && unauthorizedStatuses.includes(response.status)) {
      onUnauthorized();
      throw new Error('会话已过期，请重新登录。');
    }
    let result;
    try { result = await response.json(); }
    catch { throw new Error(`服务返回了无法解析的响应（HTTP ${response.status}）`); }
    if (!response.ok || result?.ok !== true) throw new Error(result?.error || `请求失败（HTTP ${response.status}）`);
    return result.data;
  }

  // A slow request is shared, never overlapped. Stopping invalidates its result;
  // reopening waits for that read to settle before requesting fresh state.
  function createPoller({ read, render, onError, isActive, delay }) {
    let timer = null, inFlight = null, running = false, generation = 0;
    function stop() {
      running = false; generation++;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    }
    function schedule() {
      if (!running || timer !== null || inFlight || !isActive()) return;
      timer = setTimeout(() => { timer = null; refresh(); }, delay);
    }
    async function refresh() {
      const current = generation;
      if (inFlight) {
        const prior = inFlight;
        await prior.promise;
        if (current !== generation || !isActive()) return;
        if (prior.generation === current) return;
        return refresh();
      }
      if (!isActive()) return;
      const record = { generation: current };
      inFlight = record;
      record.promise = Promise.resolve().then(read).then(data => {
        if (current === generation && isActive()) render(data);
      }).catch(error => {
        if (current === generation && isActive()) { stop(); onError(error); }
      }).finally(() => {
        if (inFlight === record) inFlight = null;
        schedule();
      });
      return record.promise;
    }
    return Object.freeze({ start() { running = true; schedule(); }, stop, refresh });
  }
  scope.WorkspaceData = Object.freeze({ requestJson, createPoller, stages, coverageStatuses, coverageUnits });
})(typeof window === 'undefined' ? globalThis : window);
