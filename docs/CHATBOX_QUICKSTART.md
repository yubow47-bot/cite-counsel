# 本机运行

一个 Python 服务同时提供页面和接口，不需要启动 Next.js。设计见 [LEGAL_TOOL_PLATFORM_BLUEPRINT.md](LEGAL_TOOL_PLATFORM_BLUEPRINT.md)。

## 启动

在项目目录的 PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe run_chatbox.py
```

打开 <http://127.0.0.1:8001>，保持启动窗口运行，按 Ctrl+C 停止。也可以运行 `./start-chatbox.ps1`；服务已在后台运行时用 `./start-chatbox.ps1 -Restart` 重启（只会停止命令行匹配本项目 `run_chatbox.py` 的进程）。

## 界面

- **对话框**：直接输入。模型按输入决定加载哪个插件、调用哪个工具；每次调用会显示一行标记。
- **设置栏**：
  - 模型 ID 与 OpenRouter API Key。Key 写入项目根目录 `.env`，立即生效、不回显。
  - 插件列表：开关、说明、工具列表、插件自己的设置项。启用只表示"可以被模型选用"，不会自动运行。
- **右侧面板**：只有插件注册了面板时才出现。

端口和上传上限在 `config/chatbox.local.json`（字段见 `config/chatbox.example.json`）；密钥只放 `.env`。

## 当前插件

| 插件 | 类别 | 默认 | 说明 |
|---|---|---|---|
| `web` | 提取 | 关 | 网络搜索与网页读取；搜索服务需要在设置栏里选择 |
| `deadlines` | 功能 | 关 | 期限日期计算，所有输入回显 |

数据源插件（A2AJ、LEGISinfo、Crossref、Open Library）和 McGill 格式化、引语核对、参考文献等功能插件按设计文档逐步加入。

## 边界

- 页面只监听本机 `127.0.0.1`。配置 Key 后，对话内容会发送给 OpenRouter 及其模型服务商；插件会访问外部数据库和网站，请勿提交不允许外发的材料。
- 会话在内存中，服务重启即丢失。
- `daily_spend_cap_usd` 是进程内的估算阈值，不是账单硬上限；需要严格控制时，请在 OpenRouter 给 Key 设置限额。

## 安装与测试

另一台电脑需要先安装 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
```

离线回归（虚拟凭据，禁止外部网络连接）：

```powershell
.\.venv\Scripts\python.exe scripts/test_chatbox_offline.py
```
