# 当前源码验证记录

## 2026-10-23 合并前验证

验证对象为当前源码；测试在隔离副本中执行，不复制本机配置、平台会话或业务数据库，不连接真实模型和平台。本报告仅保留开发验证结论，不包含真实任务、采集结果或账号记录。

| 检查 | 结果 | 范围 |
|---|---|---|
| Python 全量单元与集成 | **422 项通过，22.261 秒** | Python 3.12.14；临时数据库、本机 HTTP、模拟模型和平台 |
| 前端 Node 行为测试 | **10 组通过** | 数据访问、界面状态、合成样本、文本展示、阶段提示、工作台、服务模式、工作区、维护后台、模型超时设置 |
| 隔离 Chrome | **3 组通过** | `test_workbench_browser.cjs`、`test_evidence_ui_browser.cjs`、`test_image_evidence_browser.cjs`；临时上下文、模拟 API |
| Python 语法 | **68 个文件通过 AST 解析** | 当前脚本及 Python 测试 |
| Git 空白检查 | **通过** | `git diff --check` |

上传前按 SHA-256 核验，业务源码、前端与测试均与上述审查副本一致。随后调整源码打包白名单、忽略规则和文档，并将历史回放测试改为合成样本，以确保交付不包含本机数据；这些调整的专项检查单独记录。

## 干净交付专项验证

- 历史回放与来源编号已替换为明确合成样本；`test_agent_harness.py` 14 项、`test_source_contact_integrity.py` 8 项，共 **22 项通过**，保留原来的业务断言和数字边界覆盖。
- 源码打包三项测试通过，覆盖白名单、文件哈希、敏感内容拒绝和相对文档链接；本机安装元数据不再属于可交付文件。
- 在临时目录仅复制示例配置和 Python 源码，清空继承的配置环境后调用真实初始化流程；HTTP 监听构造使用模拟对象，没有启动网络服务。
- 连续初始化两次结果一致：内置 Prompt 共 9 条；其余 22 张业务及系统表全部为空，包括任务、笔记、评论、候选、反馈、审核、触达、模型调用、评测、用户和会话。
- 模型密钥、管理员密码哈希为空，没有创建登录 profile 或 Cookie。未读取本机配置和数据库，网络及子进程调用均为 0。
- 上传排除本地配置、数据库、平台会话、截图图库、日志、导出结果、安装元数据和历史业务运行记录。本机已有运行文件保留，不因源码整合而删除。

## 已确认但尚未修复的问题

测试通过不表示下列问题已经解决。当前合并保留这些问题，避免把审查结果误写为修复完成。

| 严重程度 | 位置 | 问题与后续处理 |
|---|---|---|
| 严重 | `scripts/workbench_store.py` | 人工审核依据未绑定图片哈希、描述和不确定性；图片证据变化可能沿用旧审核。应将完整图片证据纳入审核指纹。 |
| 中等 | `scripts/agent_harness/pipeline.py` | 低置信度 OCR 可触发硬排除并停止评论分析。应保留待核验状态，避免把不确定识别当成确定原文。 |
| 中等 | `scripts/agent_harness/runtime.py`、`assets/workbench.js` | 凭据值含 URL 及前端 Basic 认证等边界过滤不完整。应统一完整凭据字段的处理，保留正常业务原文。 |
| 中等 | `scripts/note_images.py` | 聚合读取与解压可能使总超时、取消及下载大小限制滞后。应约束原始流、总截止时间和解压大小。 |
| 中等 | `scripts/douyin_adapter.py` | 抖音图片未映射为图片提取所需的统一字段。应补齐标准化及图片识别回归。 |
| 中等 | `scripts/agent_harness/feedback.py` | 反馈冻结上下文丢失图片状态、哈希及视觉归属证据。应保留与实际判断一致的证据。 |
| 中等 | `scripts/agent_harness/evaluator.py` | 评测记录包含的图片上下文未全部传给实际模型。应使用生产路径一致的输入投影。 |
| 中等 | `scripts/agent_harness/evaluator.py`、`scripts/workbench_store.py` | 评测启用证明未绑定图片证据策略版本。策略变化后必须重新评测。 |
| 中等 | `assets/workbench.js` | Excel 与 Markdown 的笔记导出未完整包含 OCR 内容及图片链接。应共享导出记录转换。 |
| 轻微 | `assets/workbench.js`、`assets/index.html` | 仍有未使用的阶段覆盖和事件渲染函数。应删除无调用实现及失效测试断言。 |
| 轻微 | `scripts/agent_harness/runtime.py`、`scripts/llm_client.py` | 审计消息未完全反映传输层追加与重试修复消息。应按实际请求尝试保存审计。 |

## 测试入口

在 `xhs-ops-demo-skill/` 下，使用已安装依赖的 Python 环境执行：

```bash
python -B -m unittest discover -s tests -v
python -B scripts/package_release.py --self-test
```

前端内存测试通过 Node 执行对应 `tests/test_*.cjs`；三项浏览器测试需要已有 Playwright Node 库与 Chrome。浏览器测试使用模拟数据及独立上下文，不需要用户平台登录。

## 未验证范围

未在本轮执行真实采集、实际模型请求、真实平台发送、Windows/PowerShell 实机或长期大规模负载验收。模拟回归不证明平台选择器、模型分类质量或业务效果。正式验收要求见 [验收模板](acceptance.md)。
