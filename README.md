# 开放获客工作台

这是一个本地运行的开放获客工作台。它通过 `xiaohongshu-cli` 只读扫描小红书公开笔记与评论，并由多 Agent Harness 理解用户输入的需求描述、规划关键词、筛选帖子、识别可人工跟进线索和生成获客报告。

当前版本只支持本地 SQLite，不再部署服务器，也不再提供其他数据库后端。

## 本地启动

Windows:

```powershell
cd xhs-ops-demo-skill
.\start-windows.ps1
```

macOS:

```bash
cd xhs-ops-demo-skill
chmod +x start-macos.sh
./start-macos.sh
```

Linux:

```bash
chmod +x start-linux.sh
./start-linux.sh
```

启动脚本会自动创建本地 `.runtime/.venv`，安装二维码登录依赖，启动服务，并打开：

```text
http://127.0.0.1:8765/index.html
```

Harness 控制台：

```text
http://127.0.0.1:8765/harness.html
```

首次打开 Harness 时，如果还没有管理员，会提示初始化本机管理员密码。

## Skill 安装

同时安装到 Codex 和 WorkBuddy：

```bash
cd xhs-ops-demo-skill
python scripts/install_skills.py
```

安装目标：

```text
~/.codex/skills/open-leads-workbench
~/.workbuddy/skills/open-leads-workbench
```

安装脚本会排除本地数据、日志、导出、缓存和 `config.local.json`。

## 本地数据

首次启动会生成：

```text
xhs-ops-demo-skill/config.local.json
xhs-ops-demo-skill/data/sales_leads.db
xhs-ops-demo-skill/.runtime/.venv/
```

这些文件都只留在本机，不提交到 Git。

## 合规边界

- 只读调用 `xhs status/search/read/comments`。
- 不自动评论、私信、关注、点赞、收藏或发布。
- 遇到验证码、风控、IP 异常或登录过期时停止任务，由人工处理。
- 输出线索仅供账号使用者人工核验与跟进。

## 测试

```powershell
cd xhs-ops-demo-skill
python -m unittest discover -s tests -v
```
