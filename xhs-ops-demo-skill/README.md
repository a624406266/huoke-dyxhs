# 开放获客自动化工作台

本工具面向外部团队和多行业场景，用于从小红书公开笔记与评论中发现与用户需求描述匹配的可人工跟进线索。

当前版本是本地多 Agent Harness：页面、后端、SQLite 数据库、二维码登录依赖和运行日志都在本机。Chief Orchestrator 会先理解开放需求并制定策略，再分发给搜索规划、相关性判断、线索识别、产品策略、QA 和报告 Agent。未配置 LLM API Key 时任务会明确失败，不退回规则打分伪装 AI。

## 启动

Windows:

```powershell
.\start-windows.ps1
```

macOS:

```bash
chmod +x start-macos.sh
./start-macos.sh
```

Linux:

```bash
chmod +x start-linux.sh
./start-linux.sh
```

启动器会：

- 创建 `.runtime/.venv`
- 安装 `requirements-local.txt` 中的二维码登录依赖
- 创建 `config.local.json`
- 启动 `scripts/server.py`
- 自动打开 `http://127.0.0.1:8765/index.html`

如需不自动打开页面：

```bash
NO_OPEN=1 ./start-macos.sh
```

## 依赖

默认自动安装：

```text
xiaohongshu-cli==0.6.4
qrcode[pil]>=7.4,<8
```

浏览器登录助手需要本机 Chrome/Chromium，并通过 Playwright Python 包连接调试端口。当前 `xiaohongshu-cli` 依赖链可能会安装 Playwright Python 包，但启动器不会默认下载浏览器运行时。需要显式确保 Playwright 包时：

```bash
WITH_PLAYWRIGHT=1 ./start-macos.sh
```

Windows:

```powershell
.\start-windows.ps1 -WithPlaywright
```

## 页面

销售工作台：

```text
http://127.0.0.1:8765/index.html
```

Harness 控制台：

```text
http://127.0.0.1:8765/harness.html
```

销售页是本地单用户模式，打开即可使用。Harness 保留管理员登录；首次打开时如果没有管理员，会提示设置本机管理员密码。

## 配置

真实配置写入 `config.local.json`，不会提交：

- `llm.api_key`：OpenAI 兼容接口密钥
- `llm.api_base`：OpenAI 兼容接口地址
- `llm.model`：多 Agent Harness 使用的模型
- `xhs.bin`：本机 `xhs` 或 `xhs.ps1` 路径，可留空让启动器探测
- `storage.db_path`：SQLite 文件路径，默认 `data/sales_leads.db`
- `sales.redact_user`：是否打码展示公开昵称和用户 ID

也可通过环境变量覆盖：

```bash
LLM_API_KEY=你的_Key
LLM_API_BASE=https://poolrouter.com/v1
LLM_MODEL=gemini-3.5-flash
XHS_BIN=/path/to/xhs
```

## Skill 安装

```bash
python scripts/install_skills.py
```

安装到：

```text
~/.codex/skills/open-leads-workbench
~/.workbuddy/skills/open-leads-workbench
```

Skill 包含 `SKILL.md`、`agents/openai.yaml`、WorkBuddy `_user_meta.json`、启动脚本、依赖 bootstrap 和完整本地工作台代码。安装时会排除本地 SQLite、日志、导出、缓存和 `config.local.json`。

## 合规边界

- 只读操作：`xhs status` / `xhs search` / `xhs read` / `xhs comments`
- 不自动评论、私信、关注、点赞、收藏、发布
- 遇到验证码、风控、登录过期时停止任务并提示人工处理
- 导出的线索仅限使用者人工核验与跟进使用

## 输出

- 可跟进线索表：评分、优先级、需求摘要、推荐产品、原文证据、首句话术、来源链接
- 来源笔记表：标题、作者、互动数据、发布时间、链接
- 结构化审计日志：关键词、帖子、评论的保留/丢弃/忽略原因
- Markdown 获客报告
- Harness 面板：Agent 执行轨迹、任务总结、Prompt/Skill 版本与反馈学习

## 测试

```bash
python -m unittest discover -s tests -v
```
