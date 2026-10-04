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
| `a2aj` | 数据源 | 开 | 加拿大判例、法律检索及判例全文 |
| `legisinfo` | 数据源 | 开 | 加拿大联邦法案检索 |
| `crossref` | 数据源 | 开 | DOI 与期刊文章元数据 |
| `openlibrary` | 数据源 | 开 | ISBN 与图书元数据 |
| `file` | 提取 | 开 | 上传文件及图片内容提取 |
| `web` | 提取 | 关 | 网络搜索与网页读取；搜索服务需要在设置栏里选择 |
| `mcgill` | 功能 | 开 | 从记录生成引用，检查缺失字段 |
| `quote` | 功能 | 开 | 对照数据库判例全文核对引语、定位段落 |
| `bibliography` | 功能 | 开 | 从已存引用生成参考文献 |
| `deadlines` | 功能 | 关 | 期限日期计算，所有输入回显 |

以上插件均已实现。默认状态适用于尚未保存用户设置的情况；启用后仍由模型决定是否加载和调用。数据库可能缺少元数据，外部服务也可能失败。引用来源状态、格式检查和回复溯源均不证明法律结论正确，也不表示覆盖全部 McGill 规则。

## 边界

- 页面只监听本机 `127.0.0.1`。配置 Key 后，对话内容会发送给 OpenRouter 及其模型服务商；插件会访问外部数据库和网站，请勿提交不允许外发的材料。
- 会话持久化到本地 `.chatbox-runtime/sessions/`，包括 JSON 快照、模型与工具事件日志、附件及网页响应快照。后端收到有效会话 ID 和令牌时，可在服务重启后恢复未过期会话，记录编号保持有效；但当前界面在页面加载时会新建聊天并尝试删除旧会话，不恢复可见聊天历史。服务器只保存令牌哈希；闲置期限为 4 小时，启动时清理过期会话。点击“New chat”会删除旧会话及其材料，并非保存聊天归档。这些本地文件可能包含敏感内容。
- `daily_spend_cap_usd` 是进程内的估算阈值，不是账单硬上限；需要严格控制时，请在 OpenRouter 给 Key 设置限额。

## 安装与测试

另一台电脑需要先安装 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```
