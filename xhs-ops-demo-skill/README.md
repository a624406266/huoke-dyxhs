# 获客工作台：运行与配置

本目录包含工作台前后端、平台适配器和测试。项目功能概览见 [仓库首页](../README.md)，完整操作流程见 [用户手册](../docs/keyword-workbench-v3/user-guide.md)。

## 环境与启动

需要 Python 3.10+。网页登录、验证和单条触达需要本机安装 Google Chrome；Linux 还需要可用的桌面环境。Windows 已提供启动脚本，尚未完成实机验证。

从仓库根目录进入应用目录，再执行对应启动命令：

```bash
cd xhs-ops-demo-skill
```

| 系统 | 命令 |
|---|---|
| macOS | `bash start-macos.sh` |
| Linux | `bash start-linux.sh` |
| Windows PowerShell | `.\start-windows.ps1` |

启动器在 `.runtime/.venv/` 创建或复用 Python 环境，按 `requirements-local.txt` 安装依赖，并创建 `config.local.json`。首次运行需要联网；不会全局安装 Python 包，也不会安装 Chrome。

默认入口：

- [工作台](http://127.0.0.1:8765/index.html)
- [维护后台](http://127.0.0.1:8765/harness.html)

首次进入工作台时，点击登录框下方的“设置管理员”，在维护后台创建管理员密码，再配置模型、登录平台。项目没有默认密码；新安装只初始化空业务数据库及内置提示词，不导入测试样本。

### 指定 Python、端口或关闭自动打开浏览器

macOS / Linux 可使用环境变量，例如：

```bash
PYTHON_BIN=/path/to/python3 PORT=8766 NO_OPEN=1 bash start-macos.sh
```

Linux 将脚本名替换为 `start-linux.sh`；`PYTHON_BIN` 填写本机实际 Python 路径。

Windows PowerShell：

```powershell
$env:PYTHON_BIN = "C:\Path\To\Python\python.exe"
.\start-windows.ps1 -Port 8766 -NoOpen
```

指定端口后访问对应端口的页面。启动器只允许本机地址；同一数据库不能由两个服务实例同时使用。

## 模型配置

在维护后台的“系统设置”填写：

| 字段 | 内容 |
|---|---|
| `llm.api_base` | 模型服务提供的兼容接口根地址；客户端会在其后追加 `/chat/completions` |
| `llm.model` | 模型服务提供的模型标识 |
| `llm.api_key` | 对应服务的 API Key；本机回环服务可不填 |

接口需兼容 Chat Completions，模型能够按提示返回结构化 JSON。客户端默认请求流式响应，也可解析兼容服务返回的完整 JSON 响应。图片分析还需要支持图片输入；仅支持文本的模型无法完成图片识别。远程接口必须使用 HTTPS，本机回环地址允许 HTTP。

可使用 `LLM_API_BASE`、`LLM_MODEL`、`LLM_API_KEY` 环境变量覆盖本地配置。项目没有预设外部模型服务，模型调用按所选服务计费。

### 超时设置

| 配置 | 默认值 | 说明 |
|---|---|---|
| `llm.connect_timeout_s` | 30 秒 | 建立连接的等待上限 |
| `llm.read_timeout_s` | 60 秒 | 等待首段响应或相邻流式数据的最长间隔 |
| `llm.total_timeout_s` | 300 秒 | 单次结构化分析的总预算，包含最多 3 次尝试、格式修复和重试等待 |

设置值为 1–86400 的整数秒。总时限针对一次模型分析，不是整个检索任务；超时后可查看已有结果和未完成阶段，再调整采集规模或时限。

## 平台接入

### 小红书

默认启用小红书。启动器会安装清单中的 `xiaohongshu-cli`，并检测项目虚拟环境中的 `xhs` 可执行文件。需要指定其他安装位置时，可设置 `XHS_BIN` 或配置 `xhs.bin`。

在工作台使用“小红书登录 / 验证”完成登录。遇到二维码过期、验证码、滑块或账号异常时，按平台官方流程处理。

读取登录与网页触达是不同的使用条件。需要跟进操作时，应在工作台管理的 Chrome 窗口中确认账号；日常浏览器中的登录状态不会自动成为工作台的触达身份。

### 抖音接入

抖音是可选适配，需要另外准备：

- OpenCLI 及 SocialDataX 插件。
- 可用的 SocialDataX API Key，默认通过环境变量 `SOCIALDATAX_API_KEY` 读取。
- 如需网页触达，还需在工作台管理的 Chrome 窗口中登录抖音。

OpenCLI 位置可通过 `OPENCLI_BIN` 或 `opencli.bin` 指定。`socialdatax.api_key_env` 只填写存放密钥的**环境变量名称**，不要在该字段粘贴密钥。

这些工具和服务不包含在默认 Python 安装中。当前抖音读取每个关键词只支持一页；包含抖音的平台计划设置多页会被拒绝。读取服务可用与网页账号登录需分别检查。

## 本地文件与数据

以下路径相对于本应用目录：

| 路径 | 用途 |
|---|---|
| `config.example.json` | 无凭据的配置模板，可纳入版本管理 |
| `config.local.json` | 本机模型与应用配置 |
| `data/keyword_workbench_v3.db` | 任务、来源、审核、评测和动作记录 |
| `data/` 下的平台 profile | 平台登录状态和浏览器资料 |
| `.runtime/.venv/` | 项目 Python 环境 |

本机配置、数据库、登录资料、日志及导出文件已从 Git 和源码包中排除。备份或迁移时请自行保管这些文件；只复制仓库源码不会携带原有任务和账号。

业务原文、来源编号、昵称和联系方式按功能需要保留在分析与导出中。模型分析会将必要内容发送到配置的服务，图片分析也会发送图片；本地保存数据不代表所有处理都离线进行。

## 常见问题

| 现象 | 处理方式 |
|---|---|
| 找不到 Python 或版本过低 | 安装 Python 3.10+，或通过 `PYTHON_BIN` 指向已有的合适解释器 |
| 首次依赖安装失败 | 检查网络、Python 版本与安装错误；修复后重新运行启动脚本 |
| 8765 端口被其他程序占用 | 指定其他端口；工作台不会接管其他服务 |
| 模型请求失败或超时 | 核对接口地址、模型名称、Key、服务额度和超时设置；先使用较小的采集上限 |
| 文字正常但图片识别失败 | 核对模型是否支持图片输入，并检查图片读取状态；回到原图人工确认 |
| 平台登录成功但触达不可用 | 检查受管 Chrome 窗口中的实际账号、页面权限和目标定位条件 |
| 任务显示部分完成 | 展开任务说明，查看缺少正文、时间不确定、模型超时等具体阶段；已保存结果仍可查看 |
| 触达状态为未知 | 到平台核对是否已发送，避免直接重复提交 |

图片识别、平台网页操作和模型判断仍存在使用边界。已确认的问题及测试范围见 [验证记录](../docs/keyword-workbench-v3/test-report.md)。

## 开发与验证

后端为 Python HTTP 服务和 SQLite；前端为原生 HTML、CSS 与 JavaScript，无需前端构建即可运行。Node.js 仅用于前端测试。

下列命令在本目录执行，并使用启动器准备好的 Python 环境。

macOS / Linux：

```bash
.runtime/.venv/bin/python -B -m unittest discover -s tests -v
.runtime/.venv/bin/python -B scripts/package_release.py --self-test
```

Windows PowerShell：

```powershell
.\.runtime\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.runtime\.venv\Scripts\python.exe -B scripts/package_release.py --self-test
```

前端行为测试位于 `tests/test_*.cjs`。浏览器测试需要额外准备 Playwright Node 库和 Chrome；测试使用临时上下文及模拟 API。测试范围与结果见 [验证记录](../docs/keyword-workbench-v3/test-report.md)。

### 生成源码包

使用同一 Python 环境运行：

```bash
.runtime/.venv/bin/python -B scripts/package_release.py
```

Windows 使用 `.\.runtime\.venv\Scripts\python.exe` 执行同一脚本。源码包生成到仓库根目录 `dist/`，同时输出文件清单和 SHA-256。打包工具按白名单收集源码并检查文档链接，排除本机配置、数据库、登录态、依赖环境和真实业务数据。
