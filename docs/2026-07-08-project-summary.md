# 2026-07-08 项目整理总结

## 项目定位

本项目当前定位为“本地开放获客工作台”。它面向不同团队、行业和外部使用者，不再绑定某个内部销售身份。使用者输入自然语言需求描述后，系统通过本地 `xiaohongshu-cli` 采集小红书公开笔记与评论，再由多 Agent Harness 完成需求理解、搜索规划、相关性判断、线索识别、产品策略、质检复盘和报告生成。

当前版本以本地运行为主：

- 数据库：SQLite
- 页面入口：`index.html` 销售工作台、`harness.html` Agent Harness 控制台
- 运行方式：本地脚本启动服务，浏览器打开 `http://127.0.0.1:8765/index.html`
- Skill：支持安装到 Codex 和 WorkBuddy
- 部署策略：不依赖服务端采集，不再要求 PostgreSQL

## 核心能力

### 1. 开放需求获客

- 主输入从固定“业务方向”改为开放的 `需求描述`。
- `Chief Orchestrator` 先理解用户需求，再组织后续 Agent。
- `Planning Agent` 根据需求描述生成搜索策略、目标客户画像、排除项、关键词和商机假设。
- 种子词、行业、地区、采集数量、阈值、时效等保留为辅助约束。
- 如果需求超出现有产品目录，产品策略会标记“未匹配现有产品/需人工确认供给”，避免强行映射。

### 2. 多 Agent Harness

当前 Harness 包含：

- 总控编排 Agent
- 搜索规划 Agent
- 相关性判断 Agent
- 线索识别 Agent
- 产品策略 Agent
- 质检风控 Agent
- 报告 Agent
- 复盘进化 Agent
- 反馈学习 Agent

Agent 执行过程会写入 `agent_runs`、`task_events`、`task_summaries` 和结构化审计日志，便于复盘每次任务为什么找到或过滤某条内容。

### 3. 结构化审计日志

审计日志独立于普通任务事件，保存在 `audit_decisions` 表中。它覆盖：

- 搜索采集：关键词、候选笔记、去重、时间过滤、解析失败
- 相关性判断：笔记 `keep/drop/missing_decision`
- 线索识别：评论或作者级候选 `lead/ignore/below_threshold`
- 产品策略：线索更新、缺失补齐
- QA、报告、复盘：阶段级审计
- 触达辅助：关注请求、关注成功/失败、私信草稿复制

Harness 控制台可按任务、阶段、对象类型、决策和关键词筛选审计日志。

### 4. 任务 3 后的 Agent 运行优化

本轮已针对任务 3 暴露的问题完成迭代：

- Relevance Agent 必须覆盖每个输入笔记。
- Lead Signal Agent 必须覆盖每个输入评论或作者级候选。
- Product Strategy Agent 必须覆盖每条进入产品策略阶段的线索。
- Pipeline 增加缺失决策重试机制，重试后仍缺失会写入 `missing_decision`。
- 支持 `note_author` 作者级线索，避免漏掉笔记正文里的高意图需求。
- 新增地域约束后处理，深圳等城市需求可识别异地风险。
- 每篇评论扫描上限可配置，默认 50，并支持 cursor 分页读取。
- 销售页、导出和报告兼容线索类型、地域状态和扫描评论数量。

### 5. 销售页与 Harness 控制台

销售页已重构为工作台体验：

- 顶部状态栏：销售账号、小红书登录、Harness 入口、任务状态
- 配置区：需求描述、种子词、行业、地区、采集范围和运行方式
- 主工作区：任务进度、KPI、线索、日报、来源笔记、执行日志
- 结果 tabs：线索、日报、来源笔记、执行日志
- 线索卡片：评分、优先级、证据、推荐产品、话术、风险、反馈入口

Harness 控制台已拆分菜单：

- 总览
- 审计日志
- Agent 轨迹
- Agent 与 Prompt
- 反馈学习
- 销售账号
- 系统设置

前端可见的 Agent 名、阶段、决策、对象类型和覆盖状态已做中文映射，底层枚举仍保留英文，方便排障。

### 6. 触达按钮合规改造

`xiaohongshu-cli` 当前支持 `follow <user_id>`，不支持私信/DM。因此项目边界调整为：

- 采集分析阶段仍只读，只调用 `status/search/read/comments`。
- 关注功能默认关闭。
- Harness 系统设置可开启“允许手动关注线索对象”。
- 开启后，销售页每条线索展示“关注”按钮。
- 用户点击关注时必须二次确认，确认信息包含当前登录账号、目标昵称、用户 ID 和来源线索。
- 后端校验销售账号隔离、线索归属、目标 `user_id` 和小红书登录态后，只执行单条 `xhs follow <user_id> --json`。
- 私信不自动发送，只提供“私信草稿”弹窗、复制话术和打开对象主页。
- 触达动作全部写入 `outreach` 审计。

禁止能力保持不变：

- 不批量关注
- 不自动关注
- 不自动评论
- 不自动私信
- 不点赞、收藏、发布或删除
- 不绕过验证码或风控

## 本地目录与关键文件

```text
xhs-ops-demo-skill/
  assets/
    index.html              # 销售工作台
    harness.html            # Agent Harness 控制台
  scripts/
    server.py               # 本地 HTTP API
    storage.py              # SQLite 存储
    xhs_adapter.py          # xhs CLI 封装
    lead_pipeline.py        # 兼容旧 Pipeline
    agent_harness/
      pipeline.py           # 多 Agent 主 Pipeline
      agents.py             # Agent 默认 Prompt
      registry.py           # Prompt 版本管理
      feedback.py           # 反馈学习
    backfill_audit_stages.py # 历史任务阶段审计补录脚本
  tests/                    # 单元测试
  config.example.json       # 示例配置
  SKILL.md                  # Codex / WorkBuddy Skill 说明
```

## 本地启动

Windows：

```powershell
cd xhs-ops-demo-skill
.\start-windows.ps1
```

macOS：

```bash
cd xhs-ops-demo-skill
chmod +x start-macos.sh
./start-macos.sh
```

Linux：

```bash
cd xhs-ops-demo-skill
chmod +x start-linux.sh
./start-linux.sh
```

页面地址：

```text
http://127.0.0.1:8765/index.html
http://127.0.0.1:8765/harness.html
```

## Skill 安装

```bash
cd xhs-ops-demo-skill
python scripts/install_skills.py
```

安装目标：

```text
~/.codex/skills/open-leads-workbench
~/.workbuddy/skills/open-leads-workbench
```

安装时会排除：

- `.runtime`
- `data`
- `logs`
- `exports`
- `config.local.json`
- `.env`

## 测试与验证

本轮已通过：

```powershell
python -m unittest discover -s xhs-ops-demo-skill/tests -v
```

覆盖重点：

- 需求描述存储和历史兼容
- Agent 覆盖率、缺失重试和审计写入
- 作者级线索保存
- 地域规则
- 审计权限和多租户隔离
- 关注开关默认关闭
- 单条关注执行与审计
- 跨销售账号禁止关注
- 私信草稿只审计复制，不自动发送
- HTML 内联脚本语法检查

## 当前注意事项

- Harness 管理员密码不应写入仓库，应通过本机初始化或环境变量配置。
- 小红书登录态、SQLite 数据库、导出文件和日志均为本机运行态，不提交 Git。
- 遇到验证码、风控、IP 异常、账号无权限时，应暂停任务，让用户在官方页面或登录流程中人工处理。
- 私信能力当前不可用，也不通过浏览器自动化绕过限制。
- 关注功能必须由管理员开启，并由使用者对每条线索逐条确认。

