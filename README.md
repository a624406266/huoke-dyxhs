# 关键词研究与线索工作台 v3

这是在用户现有仓库上改造的本地单人 Agent 工作台，源基线为 `94c3db3`。新建任务默认寻找“智能体定制开发”的委托、外包和询价需求，保留通用关键词研究模式。输入关键词和目标后先预览搜索计划；确认后再采集公开内容、核对本人需求与委托证据，并由人工复核结果。默认优先使用小红书，同时保留抖音适配。

当前交付属于**待用户登录实测的开发版本**。代码与 mock 测试不代表平台正式验收通过；尚未积累至少四周真实使用证据，100 条人工标注样本及 20 次真实任务验收仍待完成，Windows 尚未实测。

## 本机启动

仓库提供干净的初始项目，不包含数据库、任务结果、联系人、账号配置、登录状态、密钥、日志或运行截图。首次启动会创建本机配置和空数据库；需要自行设置管理员密码、配置模型并登录平台。测试中的合成样本仅用于隔离测试，不会导入实际工作台。

```bash
cd xhs-ops-demo-skill
bash start-macos.sh
```

Windows 使用 `start-windows.ps1`，Linux 使用 `start-linux.sh`。启动器会在项目 `.runtime/.venv` 中准备声明的依赖，可能需要联网下载；不会全局安装 Python 包。已有正确环境时可直接运行 `scripts/server.py`。访问本机 [工作台](http://127.0.0.1:8765/index.html)，首次使用设置本机管理员密码。

后续由用户本人登录平台、配置模型并进行真实验收。账号 Cookie、模型密钥和运行数据仅在本机配置与数据目录维护，不应提交到仓库或交付压缩包。模型分析会调用用户配置的外部服务；对发送内容和数据范围的说明见用户手册。

## 文档入口

- [本地运行与配置](xhs-ops-demo-skill/README.md)
- [架构与状态模型](docs/keyword-workbench-v3/architecture.md)
- [用户操作手册](docs/keyword-workbench-v3/user-guide.md)
- [验收标准与真实记录模板](docs/keyword-workbench-v3/acceptance.md)
- [中级申报六字段与证据清单](docs/keyword-workbench-v3/certification.md)
- [版本说明、贡献与已知限制](docs/keyword-workbench-v3/release-notes.md)
- [当前修复清单与验证结果](docs/keyword-workbench-v3/test-report.md)

## 测试与打包

```bash
cd xhs-ops-demo-skill
python3 -m unittest discover -s tests -v
python3 scripts/package_release.py --self-test
python3 scripts/package_release.py
```

最后一条命令在仓库 `dist/` 生成源码 ZIP、文件清单和 SHA-256。打包不会安装依赖、运行真实采集或触达、连接远程服务，也不会提交或推送代码。依赖环境、模型和平台登录信息需要接收方自行配置。

本次开发保留已有仓库及基线作者的贡献与历史。新增工程能力不能据此将整个项目描述成从零独立开发；申报人应如实说明自己的需求决策、开发或协作范围、实测和复盘贡献。不得通过包装、改名或改写绕过代码查重要求，也无法保证与未知查重库的重合比例。
