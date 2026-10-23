/* Presentation only: never translate payloads, user content or source evidence. */
(function (scope) {
  'use strict';
  const stages = {
    starting:'启动中',round:'分轮执行',orchestrate:'任务编排',plan:'搜索规划',planning:'搜索规划',
    search:'搜索采集',relevance:'相关性判断',details:'读取笔记详情',read_detail:'读取笔记详情',image_reading:'图片内容识别',
    comments:'评论采集',lead_signal:'需求研判',qa:'质量检查',report:'生成报告',reflection:'任务复盘',
    evolution:'版本改进',feedback:'反馈分析',outreach:'执行触达',time_filter:'发布时间核验',
    search_filter:'搜索结果筛选',pipeline:'任务流程',done:'已完成',auth:'登录检查'
  };
  const statuses = {
    running:'运行中',completed:'已完成',partial:'部分完成',needs_action:'需要人工处理',failed:'失败',
    stopped:'已停止',interrupted:'已中断',pending:'待处理',skipped:'已跳过',not_started:'未开始',
    finished:'已结束',needs_review:'待复核',awaiting_evaluation:'等待评测',candidates_ready:'候选已生成',
    no_changes:'没有新候选',active:'已启用',candidate:'候选版本',disabled:'已禁用',not_evaluated:'未评测',
    passed:'已通过',pending_human_review:'待人工验收',awaiting_frozen_dataset:'等待冻结验收样本',
    approved:'已通过',rejected:'已驳回',analyzed:'已分析',applied:'已生成候选',dismissed:'已忽略',
    prepared:'待确认执行',executing:'正在执行',succeeded:'已验证成功',unknown:'待核实',
    closed:'已关闭',waiting:'等待操作',scanned:'已扫码',confirmed:'已确认',expired:'已过期',
    error:'错误',cleared:'已清除',connecting:'正在连接',streaming:'正在接收响应',complete:'已完成',
    retry:'正在重试',success:'成功',info:'信息',warning:'警告',warn:'警告',debug:'调试信息',
    idle:'空闲',queued:'排队中',ready:'已就绪',unavailable:'暂不可用',not_available:'未提供'
  };
  const agents = {
    chief_orchestrator:'总控编排',planning_agent:'搜索规划',relevance_agent:'相关性判断',image_reading_agent:'图片内容识别',
    lead_signal_agent:'需求识别',critic_qa_agent:'质量检查',report_agent:'报告生成',
    reflection_evolution_agent:'复盘改进',feedback_learning_agent:'反馈学习',
    'Chief Orchestrator':'总控编排','Planning Agent':'搜索规划','Relevance Agent':'相关性判断',
    'Lead Signal Agent':'需求识别','Critic / QA Agent':'质量检查','Report Agent':'报告生成',
    'Reflection / Evolution Agent':'复盘改进','Feedback Learning Agent':'反馈学习',
    'Auth Agent':'登录检查','Tool Harness':'工具调用','Delay Guard':'请求间隔控制',
    'Task Manager':'任务管理','Keyword Agent':'关键词规划','Search Agent':'搜索采集',
    'Signal Agent':'需求识别','QA Agent':'质量检查','Model Request':'模型请求',
    'Model Usage':'模型用量',Evolution:'复盘改进',System:'系统',system:'系统'
  };
  const reasons = {
    no_more_results:'平台没有更多结果',no_more_unique_results:'没有更多不重复结果',
    note_quota_reached:'已达到笔记采集上限',comment_quota_reached:'已达到评论采集上限',
    configured_pages_completed:'设定页数已完成',user_stopped:'用户已停止任务',
    invalid_record:'无效记录',non_note:'非笔记内容',missing_note_id:'缺少笔记编号',duplicate:'重复内容',
    excluded_keyword:'命中排除关键词',outside_time_range:'发布时间超出范围',unknown_publish_time:'发布时间待核验',
    capacity_limit:'已达到采集上限',in_range:'在所选时间范围内',out_of_range:'超出所选时间范围',
    within_window:'发布时间在所选范围内',older_than_window:'发布时间早于所选范围',
    boundary_precision:'日期精度不足，无法确认是否在所选范围内',missing_time:'缺少发布时间',
    update_only:'仅有编辑时间，无法确认发布时间',invalid_time:'发布时间格式无效',future_time:'发布时间晚于当前时间',
    unrecognized_time:'无法识别发布时间',unsupported_time_filter:'不支持所选时间范围',unrestricted:'不限时间',parsed:'时间已解析',
    connect_timeout:'连接模型服务超时',first_response_timeout:'等待模型首次响应超时',
    stream_idle_timeout:'等待模型后续响应超时',total_timeout:'模型调用超过总时限',
    network_error:'网络连接异常',dns_error:'模型服务地址解析失败',tls_error:'安全连接校验失败',
    http_error:'模型服务返回错误',invalid_response:'模型响应格式不符合要求',incomplete_stream:'模型响应接收不完整',
    provider_error:'模型服务处理失败',output_truncated:'模型输出被截断',response_too_large:'模型响应超出处理范围',
    invalid_json:'模型返回的数据格式无法解析',validation_error:'模型输出未通过字段校验',configuration_error:'模型配置不完整或无效',
    TimeoutError:'请求超时',URLError:'服务连接失败',ConnectionError:'连接异常',ConnectionResetError:'连接被中断',
    ConnectionRefusedError:'服务拒绝连接',gaierror:'服务地址解析失败',SSLError:'安全连接校验失败',HTTPError:'服务返回错误',
    JSONDecodeError:'返回的数据格式无法解析',ValueError:'数据校验失败',OSError:'本机操作失败',LLMRequestError:'模型请求失败',
    ConnectTimeout:'连接超时',ReadTimeout:'等待响应超时',WriteTimeout:'发送请求超时',PoolTimeout:'等待可用连接超时',
    ConnectError:'连接失败',ReadError:'读取响应失败',WriteError:'发送请求失败',RemoteProtocolError:'服务响应中断',HTTPStatusError:'服务返回错误'
  };
  const classifications = {demand:'明确需求',uncertain:'待确认需求',information:'经验或知识讨论',promotion:'供方宣传',irrelevant:'无关内容'};
  const purposes = {
    'task orchestration':'任务理解与阶段编排','search planning':'搜索策略规划','batch note relevance':'批量判断笔记相关性',
    'adaptive relevance review':'相关性复核','batch lead signal detection':'批量识别需求信号','adaptive lead review':'需求复核',
    'quality and safety review':'质量与安全检查','sales report generation':'生成线索报告',
    'task reflection and evolution':'任务复盘与改进候选生成','feedback learning analysis':'人工反馈学习分析',
    'missing relevance decisions':'补齐缺失的相关性判断','missing lead decisions':'补齐缺失的需求判断'
  };
  const platforms = {xhs:'小红书',xiaohongshu:'小红书',douyin:'抖音',mixed:'多平台',all:'全部平台'};
  const others = {
    main:'任务执行',feedback:'反馈学习',evolution:'复盘改进',eval:'样本评测',acceptance:'冻结验收集',development:'调试集',
    single:'单次任务',batch:'批量任务',model:'模型生成',llm:'模型生成',manual:'人工配置',fallback:'人工补充',
    high:'高',medium:'中',low:'低',none:'未设置',general:'通用关键词研究',agent_custom_development:'智能体定制开发',
    keep:'保留',drop:'排除',ignore:'忽略',allow:'通过',pass:'通过',ok:'通过',reject:'拒绝',block:'已阻止',
    lead:'需求线索',note:'笔记',comment:'评论',keyword:'关键词',task:'任务',agent:'智能体',eval_case:'评测样本',
    note_author:'笔记作者',comment_lead:'评论线索',follow:'关注',dm:'发送私信',reply:'回复评论',
    planned:'已规划',collected:'已采集',parse_failed:'解析失败',dropped_duplicate:'已排除重复内容',
    dropped_time_filter:'已排除不符合时间范围的内容',missing_decision:'缺少判断',below_threshold:'低于筛选标准',
    updated:'已更新',generated:'已生成',recommended_action:'建议操作',risk_warning:'风险提示',review:'复盘评估',
    prompt_candidate:'提示词候选',initial:'首次返回',missing_after_retry:'重试后仍缺失',
    price_inquiry:'价格询问',service_interest:'服务意向',insufficient_context:'语境不足',
    popular:'综合排序',latest:'最新发布',likes:'点赞最多',comments:'评论最多',collects:'收藏最多',
    week:'近一周',day:'近一天',month:'近一个月',half_year:'近半年',all:'全部',
    'Built-in Harness prompt.':'系统内置提示词',matched:'已匹配',missing:'缺失',explicit:'明确',
    match:'匹配',possible:'可能匹配',mismatch:'不匹配',needs_city_confirmation:'需要确认城市'
  };
  const fields = {
    stage:'阶段',status:'状态',phase:'处理进度',code:'原因',reason:'原因',error:'错误说明',error_type:'错误类型',
    message:'说明',summary:'摘要',agentKey:'处理环节',agent_key:'处理环节',agent:'处理环节',platform:'平台',
    keyword:'关键词',source_keyword:'命中关键词',model:'模型',attempt:'尝试次数',elapsedSeconds:'已耗时（秒）',
    receivedCharacters:'已收到字符',reasoningCharacters:'分析过程字符',received:'收到内容',retained:'保留内容',
    count:'数量',total:'总计',current:'当前数量',completed:'已完成',planned:'计划数量',failed:'失败数量',
    skipped:'已跳过',missing:'缺失数量',inputCount:'输入数量',outputCount:'输出数量',coveredCount:'已覆盖数量',
    missingCount:'缺失数量',duplicateCount:'重复数量',unknownCount:'未知数量',coverageBasis:'覆盖依据',
    filterCounts:'过滤统计',timeResolution:'发布时间核验',attempted:'已核验',resolved:'已确认',unresolved:'待核验',
    interrupted:'中断数量',durationMs:'耗时（毫秒）',confidence:'置信度',score:'评分',decision:'判断',
    classification:'需求类别',risk:'风险提示',purpose:'用途',usage:'模型用量',prompt_tokens:'输入用量',
    completion_tokens:'输出用量',total_tokens:'总用量',promptTokens:'输入用量',completionTokens:'输出用量',totalTokens:'总用量',
    noteId:'笔记编号',note_id:'笔记编号',taskId:'任务编号',callId:'调用编号',runId:'执行编号',
    target_id:'目标编号',requested_action:'建议操作',question:'待确认事项',evidence:'原文证据'
  };
  const text = value => value == null ? '' : String(value);
  const hasChinese = value => /[\u3400-\u9fff]/.test(value);
  function lookup(map, value, fallback) {
    const raw = text(value).trim();
    return map[raw] || (hasChinese(raw) ? raw : fallback);
  }
  const status = value => lookup(statuses,value,'状态待确认');
  const stage = value => lookup({...statuses,...stages},value,'阶段待确认');
  const agent = value => lookup({...stages,...agents},value,'系统处理').replace(/\s*Agent\b/g,'智能体');
  const platform = value => lookup(platforms,value,'其他平台');
  const classification = value => lookup(classifications,value,'待确认类别');
  const purpose = value => lookup(purposes,value,'任务处理');
  const allLabels = {...fields,...others,...classifications,...platforms,...purposes,...reasons,...stages,...statuses,...agents};
  const code = (value, fallback = '待确认') => lookup(allLabels,value,fallback);
  function reason(value) {
    const raw = text(value).trim();
    const lane = raw.match(/^(xhs|xiaohongshu|douyin):([\s\S]*):(no_more_results|no_more_unique_results)$/);
    if (lane) return `${platform(lane[1])}「${lane[2]}」：${reasons[lane[3]]}`;
    return lookup({...statuses,...reasons},raw,'原因待确认，请查看技术详情');
  }
  function stopReasons(value) {
    if (!value || (Array.isArray(value) && !value.length)) return '';
    return (Array.isArray(value) ? value : [value]).map(reason).join('；');
  }
  // Apply only at system-message boundaries; never pass note text, user notes,
  // keyword names or message drafts through this function.
  function message(value) {
    const raw = text(value).trim();
    if (!raw) return '';
    if (allLabels[raw]) return allLabels[raw];
    if (/^(xhs|xiaohongshu|douyin):[\s\S]*:(no_more_results|no_more_unique_results)$/.test(raw)) return reason(raw);
    if (raw.includes('模型服务返回的实际用量；缺失值表示未提供，非零用量')) return '模型用量未提供的项目保留为空，不按 0 计算。';
    if (/Failed to fetch|Load failed|NetworkError|ERR_CONNECTION_REFUSED|ECONNREFUSED/i.test(raw)) return '无法连接服务，请确认本地项目已启动并检查网络连接。';
    if (/opencli returned non-json output/i.test(raw)) return '平台读取工具返回了无法解析的数据，请查看技术详情。';
    if (/Timeout.*exceeded|waiting for (?:locator|selector)/i.test(raw)) return '等待平台页面响应超时，请检查登录状态和目标页面后重试。';
    if (/Target (?:page|closed)|TargetClosedError|Browser has been closed/i.test(raw)) return '浏览器或目标页面已关闭，请重新打开后操作。';
    if (/Unexpected token|Unexpected end of JSON|not valid JSON/i.test(raw)) return '服务返回的数据无法解析，请查看技术详情。';
    if (/HTTP(?: Error)?\s*401|unauthorized/i.test(raw)) return '身份验证失败，请检查登录状态或模型密钥。';
    if (/HTTP(?: Error)?\s*403|forbidden/i.test(raw)) return '服务拒绝访问，请检查账号权限。';
    if (/HTTP(?: Error)?\s*429|too many requests|rate limit/i.test(raw)) return '请求过于频繁或额度不足，请稍后重试并检查服务额度。';
    if (/HTTP(?: Error)?\s*5\d\d/i.test(raw)) return '服务暂时异常，请稍后重试。';
    // Exact known identifiers can appear inside Chinese diagnostics. Identifiers
    // and raw payloads remain unchanged in technical details and persisted data.
    const protectedParts = [];
    let result = raw.replace(/https?:\/\/[^\s<>"“”「」`，。；！、）]+|“[^”]*”|「[^」]*」/g, part => {
      protectedParts.push(part); return `\uE000${protectedParts.length-1}\uE001`;
    });
    result = result.replace(/\b[A-Za-z][A-Za-z0-9_]*\b/g, token => allLabels[token] || ({QA:'质检',Agent:'智能体',Prompt:'提示词',JSON:'结构化数据'})[token] || token)
      .replace(/\uE000(\d+)\uE001/g, (_,index) => protectedParts[Number(index)]);
    if (!hasChinese(result)) return '系统返回了需要核查的信息，请查看技术详情。';
    return result;
  }
  function humanize(value, depth = 0) {
    if (value == null || value === '') return '未提供';
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (typeof value === 'number') return String(value);
    if (typeof value === 'string') return code(value,value);
    if (depth > 8) return '详情层级较多，请查看原始记录';
    if (Array.isArray(value)) return value.map(item => humanize(item,depth+1)).join('；') || '无';
    return Object.entries(value).map(([key,item]) => {
      const label = fields[key] || reasons[key] || (hasChinese(key) ? key : '补充信息');
      const formatted = key === 'stage' ? stage(item) : key === 'status' ? status(item) : key === 'platform' ? platform(item)
        : ['agent','agentKey','agent_key'].includes(key) ? agent(item) : ['message','error','reason'].includes(key) ? message(item)
        : humanize(item,depth+1);
      return `${label}：${formatted}`;
    }).join('；') || '无';
  }
  const reportFields = {
    topLeads:'重点需求候选',noteContexts:'笔记分析明细',qualifiedCount:'合格需求数',leadsCount:'需求候选数',
    classificationCounts:'需求分类统计',filterCounts:'筛选统计',failures:'采集失败记录',endReasons:'搜索结束原因',
    timeFilter:'搜索时间范围',commentsCollected:'已采集评论数',notesCollected:'已采集笔记数',
    receivedNotes:'平台返回内容数',rawSeen:'已扫描内容数',searchRequests:'搜索次数',keywordCoverage:'关键词覆盖情况',
    stageCoverage:'各环节完成情况',collectionSummary:'采集汇总',readDetail:'读取笔记详情',commentSample:'评论采集范围',
    maxNotes:'笔记采集上限',maxComments:'评论采集上限',maxKeywords:'搜索词上限',pagesPerKeyword:'每词搜索页数',
    readTop:'详情读取范围',fetchComments:'采集评论',commentsPerNote:'每篇评论上限',maxTotalComments:'评论总上限',
    duplicates:'重复内容数',duplicate:'重复内容',outside_time_range:'超出时间范围的内容',
    invalid_record:'无效记录',non_note:'非笔记内容',missing_note_id:'缺少笔记编号的内容',
    excluded_keyword:'命中排除词的内容',unknown_publish_time:'发布时间待核验的内容',capacity_limit:'达到采集上限',
    no_more_results:'平台没有更多结果',no_more_unique_results:'没有更多不重复结果',
    configured_pages_completed:'设定页数已完成',note_quota_reached:'已达到笔记采集上限',
    comment_quota_reached:'已达到评论采集上限',reason:'判断依据',visited:'是否已搜索',exhausted:'是否已到搜索末页',
    relevance:'相关性判断',lead_signal:'需求研判',qa:'质量检查',qualified:'符合需求条件',
    leadCandidates:'待分析需求候选',source_keyword:'命中关键词',sourceRefs:'来源引用',
    review_status:'人工审核状态',qa_status:'质量检查状态',evidenceComplete:'证据是否完整',
    requested_action:'建议操作',task_summary:'任务总结',evolution_summary:'改进总结',
    classification:'需求类别',decision:'判断结论',confidence:'置信度',evidence:'原文证据',
    next_actions:'后续建议',recommended_actions:'建议操作',risk:'风险提示',risks:'风险提示',
    xhs:'小红书',douyin:'抖音',QA:'质检',Agent:'智能体',Prompt:'提示词'
  };
  // Legacy generated reports sometimes quote input field names in their prose.
  // Convert those known fields at display/export time without rewriting stored
  // model output, source quotes, URLs, brand names or user-entered keywords.
  function report(value, preservedTerms = []) {
    if (value == null) return '';
    if (typeof value !== 'string') return humanize(value);
    const protectedParts = [];
    const protect = part => { protectedParts.push(part); return `\uE000${protectedParts.length-1}\uE001`; };
    let result = value.replace(/https?:\/\/[^\s<>"“”「」`，。；！、）]+|“[^”]*”|「[^」]*」|'[^'\n]*'/g, protect);
    // Contextual keyword mentions and the confirmed task vocabulary are literal
    // user data, even when a word happens to match an internal field.
    result = result.replace(/((?:关键词|搜索词|种子词)(?:为|是|[:：])?\s*)([A-Za-z][A-Za-z0-9_+.-]*)/g, (_,prefix,term) => prefix + protect(term));
    const terms = (Array.isArray(preservedTerms) ? preservedTerms : [preservedTerms]).filter(term => typeof term === 'string' && term.trim()).sort((a,b) => b.length-a.length);
    for (const term of terms) {
      const escaped = term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
      const pattern = new RegExp(`(^|[^A-Za-z0-9_])(${escaped})(?=$|[^A-Za-z0-9_])`,'g');
      result = result.replace(pattern, (_,prefix,literal) => prefix + protect(literal));
    }
    const structuredText = data => {
      if (data === null) return '未提供';
      if (typeof data === 'boolean') return data ? '是' : '否';
      if (typeof data !== 'object') return code(data,String(data));
      if (Array.isArray(data)) return data.map(structuredText).join('、') || '无';
      return Object.entries(data).map(([key,item]) => `${reportFields[key] || code(key,'其他项目')}：${structuredText(item)}`).join('；') || '暂无记录';
    };
    result = result.replace(/\b([A-Za-z][A-Za-z0-9_]*)\s*[=:：]\s*(\{[^{}]*\}|\[[^\[\]]*\])/g, (whole,field,json) => {
      if (!reportFields[field]) return whole;
      try {
        const data = JSON.parse(json);
        if (!Object.keys(data).length) return field === 'failures' ? '未记录采集失败' : `暂无${reportFields[field]}`;
        return `${reportFields[field]}（${structuredText(data)}）`;
      } catch (_) { return whole; }
    });
    // Literal quoted evidence is preserved, including a one-word quote such as
    // "reason". Unquoted known generated fields are translated below.
    result = result.replace(/"[^"\n]*"/g, protect);
    result = result.replace(/`([^`]+)`/g, (whole, inner) => /\b(?:topLeads|noteContexts|qualifiedCount|leadsCount|visited|exhausted|commentsCollected|classificationCounts|filterCounts|failures|endReasons|no_more_results|timeFilter)\b/.test(inner) ? inner : whole);
    result = result.replace(/\bvisited\s*[=:：]\s*true\b/gi,'已执行搜索')
      .replace(/\bvisited\s*[=:：]\s*false\b/gi,'尚未执行搜索')
      .replace(/\bexhausted\s*[=:：]\s*true\b/gi,'平台没有更多结果')
      .replace(/\bexhausted\s*[=:：]\s*false\b/gi,'尚未标记搜索结束')
      .replace(/\b(timeFilter|sort|qa_status|review_status|classification)\s*[=:：]\s*([A-Za-z_]+)\b/g, (_,field,setting) => `${reportFields[field] || '排序'}：${code(setting,'待确认')}`)
      .replace(/\b(readDetail|fetchComments|qualified|evidenceComplete)\s*[=:：]\s*(true|false)\b/gi, (_,field,enabled) => `${reportFields[field]}：${enabled.toLowerCase() === 'true' ? '是' : '否'}`)
      .replace(/\bfailures\s*[=:：]\s*\[\s*\]/g,'未记录采集失败')
      .replace(/\b(topLeads|noteContexts|classificationCounts|endReasons)\s*[=:：]\s*(?:\[\s*\]|\{\s*\})/g, (_,field) => `暂无${reportFields[field]}`)
      .replace(/\b(qualifiedCount|leadsCount|commentsCollected|notesCollected|receivedNotes|duplicate|duplicates|outside_time_range|unknown_publish_time|invalid_record|non_note|missing_note_id|excluded_keyword)\s*[=:：]\s*(\d+)\b/g, (_,field,count) => `${reportFields[field]}为 ${count} 条`)
      .replace(/\b[A-Za-z][A-Za-z0-9_]*\b/g, token => reportFields[token] || token);
    return result.replace(/\uE000(\d+)\uE001/g, (_,index) => protectedParts[Number(index)]);
  }
  scope.DisplayText = Object.freeze({stage,status,agent,reason,stopReasons,message,code,platform,classification,purpose,humanize,report});
})(typeof window === 'undefined' ? globalThis : window);
