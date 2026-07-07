---
name: open-leads-workbench
description: 本地开放获客工作台。Use when 用户要启动、配置或排查本地小红书只读获客工作台，打开本地 HTML 页面，通过 SQLite、xiaohongshu-cli 二维码登录和多 Agent Harness 分析公开笔记与评论，生成线索、审计日志和获客报告。
---

# 开放获客工作台

用于外部团队和多行业场景，从小红书公开内容中发现与开放需求描述匹配的可人工跟进线索。当前版本是本地多 Agent Harness：总控 Agent 统一派活，子 Agent 执行规划、相关性判断、线索识别、产品策略、QA、报告和复盘进化。

## 能做什么

- 启动本地获客工作台
- 由 `Chief Orchestrator` 拆解任务并派发给多个 LLM Agent
- 由 `Planning Agent` 生成关键词包和搜索假设
- 只读搜索小红书笔记、读取详情和评论
- 由 `Relevance Agent` 判断笔记相关性
- 由 `Lead Signal Agent` 识别采购、报价、部署、迁移、降本、技术选型等跟进意图
- 由 `Product Strategy Agent` 匹配当前用户可选产品/服务参考；无法匹配时标记“未匹配现有产品/需人工确认供给”
- 导出 Excel 线索表和 Markdown 获客报告
- 显示 Agent trace、任务复盘、Prompt/Skill 版本和自动进化记录
- 默认展示公开评论昵称和用户 ID，方便使用者人工核验；需要时可在设置中开启隐私打码
- 将人工反馈提交到 Harness 反馈学习队列，由管理员确认后启用运行时 Prompt/Skill 新版本

## 前置依赖

首次启动会自动创建本地 `.runtime/.venv` 并安装二维码登录依赖：

- `xiaohongshu-cli==0.6.4`
- `qrcode[pil]>=7.4,<8`

也可以显式设置已有 `xhs`：

```bash
XHS_BIN=/path/to/xhs
```

还需要配置 OpenAI 兼容 LLM Key。Harness 模式下没有 Key 会明确失败，不会退回规则打分：

```bash
LLM_API_KEY=你的_OpenAI_兼容_API_Key
LLM_API_BASE=https://poolrouter.com/v1
LLM_MODEL=gemini-3.5-flash
```

## 启动

Windows：

```powershell
.\start-windows.ps1
```

macOS：

```bash
chmod +x start-macos.sh
./start-macos.sh
```

Linux：

```bash
chmod +x start-linux.sh
./start-linux.sh
```

脚本会自动打开：

```text
http://127.0.0.1:8765/index.html
```

## 合规边界

只允许只读操作：

- `xhs status`
- `xhs search`
- `xhs read`
- `xhs comments`

禁止自动执行：

- 点赞
- 收藏
- 评论
- 私信
- 关注
- 发布
- 删除

遇到验证码、风控、IP异常或登录过期时停止任务，让用户在浏览器或登录流程中人工处理。

## Harness 进化机制

- 每次任务都会保存 Agent run、结构化输出、置信度、错误、耗时和 Prompt 版本。
- `Reflection / Evolution Agent` 会生成任务总结、Agent 表现总结、评估样例和候选 Prompt 改进。
- Evaluator 会给候选版本评分；高分且安全的候选会自动启用。
- 页面可查看 Prompt 版本、手动评估、禁用当前版本，并回滚到可用版本。
- 线索反馈和漏识别反馈可由 `反馈学习 Agent` 分析；管理员确认应用后只改运行时版本，不自动修改仓库源码。

## Agent 操作建议

当用户要求使用该工作台：

1. 先运行对应平台启动脚本；脚本会准备依赖、启动本地服务并打开页面
2. 如需只打印/复用地址，打开 `http://127.0.0.1:8765/index.html`
3. 说明这是只读获客辅助工具，不会自动触达用户
4. 说明没有 `LLM_API_KEY` 时 Harness 不会运行
5. 如失败，优先检查：
   - `.runtime/.venv` 是否完成依赖安装
   - `XHS_BIN` 是否可解析
   - `xhs status` 是否已登录
   - 是否触发验证码或风控；遇到 `Captcha required` 时，引导用户点击页面里的“打开网页登录/验证”，在 Chrome 中完成官方验证后再确认登录态
   - `LLM_API_KEY` 是否配置
   - 端口是否被占用
