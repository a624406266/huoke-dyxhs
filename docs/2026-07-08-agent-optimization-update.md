# 2026-07-08 Agent 运行优化更新总结

## 背景

本轮更新基于本地 Harness 任务 3 的审计日志、Agent 轨迹和 SQLite 记录复盘结果。任务 3 能完成获客流程，但暴露出几个影响召回和解释性的问题：

- 相关性 Agent 对候选笔记的覆盖不足，输入 64 篇只返回 14 篇决策，导致大量 `missing_decision`。
- Lead Signal Agent 没有对每条评论给出逐条 `lead / ignore / below_threshold` 判断，非商机原因不够具体。
- 高意图笔记正文中的作者需求没有进入线索保存链路，例如“20W 预算、私有化部署”这类作者级需求会被漏掉。
- 深圳需求会混入江西、湖南等地域明显不匹配线索，QA 只能事后发现问题。
- 每篇笔记默认只扫前 10 条评论，大评论量笔记的线索召回不足。
- 任务结果里的评论扫描数量与实际评论表记录口径不一致。

## 本轮目标

- 强化 Agent 输出协议，让 Relevance、Lead Signal、Product Strategy 都按输入对象逐条返回结果。
- 在 Pipeline 层补齐缺失决策，降低 `missing_decision`，同时把覆盖率写入审计。
- 支持 `note_author` 作者级线索，不再只依赖评论线索。
- 增加确定性的地域约束后处理，避免目标城市需求被明显异地线索污染。
- 将每篇评论扫描上限变成可配置项，默认提升到 50 条，并支持 cursor 分页。
- 扩展审计和前端展示，让使用者能看到线索类型、地域状态、地域风险和产品策略覆盖情况。

## 主要改动

### 1. Agent 协议升级

- 将内置 Agent prompt 升级到 v2，重点强化覆盖率要求：
  - `chief-orchestrator-v2`
  - `planning-agent-v2`
  - `relevance-agent-v2`
  - `lead-signal-agent-v2`
  - `product-strategy-agent-v2`
- Relevance Agent 必须对每个输入 `note_id` 返回 `keep / drop`。
- Lead Signal Agent 必须对每个候选返回 `lead / ignore / below_threshold`、评分、置信度、原因和风险。
- Product Strategy Agent 必须对每条进入产品策略阶段的线索返回 `lead_update`。
- Runtime 增加 `outputContract` 系统约束，即使数据库中启用了旧的运行时 Prompt，也会优先遵守本轮覆盖率契约。
- Prompt registry 只在当前激活版本仍是内置版本时自动升级，避免覆盖用户或反馈学习产生的运行时版本。

### 2. Pipeline 覆盖率与重试机制

- Relevance 阶段会检测缺失笔记判断，并按小批量自动重试。
- Lead Signal 阶段会检测缺失候选判断，并按小批量自动重试。
- Product Strategy 阶段会检测缺失线索更新，并补齐产品策略。
- 重试后仍缺失的对象会写入明确的 `missing_decision` 审计，不再静默丢失。
- 审计 payload 增加 `coverage_status`，用于区分 `initial`、`retry`、`missing_after_retry`。

### 3. 作者级线索

- 新增 `note_author` 线索类型。
- 对包含预算、报价、成本、私有化、本地部署、接入、采购、方案等高意图表达的笔记正文，生成作者级候选。
- 作者级线索使用稳定 `lead_id = note:{note_id}`，允许 `comment_id` 为空。
- 产品策略、QA、日报、导出和销售页线索卡片均兼容 `comment_lead` 与 `note_author`。

### 4. 地域约束后处理

- 从需求描述和地区字段中抽取 `geo_constraints`。
- 对深圳等城市型需求使用确定性规则：
  - 目标城市命中：`match`
  - 同省但城市不确定：`needs_city_confirmation`
  - 无 IP 属地：`unknown`
  - 明显异地：`mismatch`
- 明显异地线索会被降到阈值以下或丢弃，并在审计中记录具体原因。
- 同省或未知地域会保留，但在风险中提示人工确认。

### 5. 评论扫描深度

- 新增配置项 `xhs.max_comments_per_note`，默认 `50`，范围 `1-500`。
- Harness 系统设置页新增“每篇最多扫描评论数”。
- XHS adapter 支持 `xhs comments --cursor`，Pipeline 按 cursor 分页读取，避免默认无限全量。
- 任务结果新增：
  - `scannedCommentsCount`
  - `maxCommentsPerNote`
  - 每篇笔记的 `scanned_comments_count`

### 6. 审计与前端展示

- Lead Signal 审计增加：
  - `lead_type`
  - `geo_status`
  - `geo_reason`
  - `coverage_status`
- Product Strategy 阶段新增结构化审计，记录每条线索是否完成产品策略更新。
- 销售页线索卡片展示线索类型和地域状态。
- 风险区展示地域说明。
- Excel 和 Markdown 导出增加线索类型、地域状态和地域说明。

## 涉及文件

- `xhs-ops-demo-skill/scripts/agent_harness/agents.py`
- `xhs-ops-demo-skill/scripts/agent_harness/pipeline.py`
- `xhs-ops-demo-skill/scripts/agent_harness/registry.py`
- `xhs-ops-demo-skill/scripts/agent_harness/runtime.py`
- `xhs-ops-demo-skill/scripts/xhs_adapter.py`
- `xhs-ops-demo-skill/scripts/config_loader.py`
- `xhs-ops-demo-skill/config.example.json`
- `xhs-ops-demo-skill/assets/index.html`
- `xhs-ops-demo-skill/assets/harness.html`
- `xhs-ops-demo-skill/tests/test_agent_harness.py`

## 测试与验证

已完成本地验证：

```powershell
python -m unittest discover -s xhs-ops-demo-skill\tests -v
```

结果：`21 tests OK`

同时完成：

- Python 语法编译检查通过。
- `index.html` 与 `harness.html` 脚本解析检查通过。
- Skill 已重新安装到 Codex 与 WorkBuddy 本地目录。
- 本地服务已重启并完成接口健康检查。
- `http://127.0.0.1:8765/index.html` 可访问。
- `http://127.0.0.1:8765/harness.html` 可访问。

## 新增回归用例

新增任务 3 回放夹具测试，覆盖：

- Relevance 首轮漏判后自动补齐，最终不产生相关性 `missing_decision`。
- 每篇评论扫描上限生效，并通过 cursor 分页读取。
- “20W 预算 / 私有化部署”笔记作者被保存为 `note_author` A 级线索。
- 广东 IP 的深圳需求线索被保留并标记 `needs_city_confirmation`。
- 江西 IP 的深圳需求线索被地域规则降到阈值以下。
- 非商机评论保留具体 ignore reason。
- Product Strategy 对所有进入阶段的线索都有 `updated` 审计。

## 使用说明

本轮更新仍保持只读采集边界，不新增自动评论、私信、点赞、关注、收藏或发布能力。

本地验证入口：

- 销售工作台：`http://127.0.0.1:8765/index.html`
- Agent Harness：`http://127.0.0.1:8765/harness.html`

建议下一轮重点观察：

- 不同需求下 `missing_decision` 是否明显下降。
- 审计日志中 `ignore / below_threshold` 的原因是否足够具体。
- `note_author` 线索是否提升高意图作者需求召回。
- `max_comments_per_note = 50` 对风控、耗时和召回的平衡是否合适。
- 地域规则是否需要扩展更多城市与省份映射。
