# 本机 HTTP Chatbox

这是独立于旧前端的快速入口：一个 Python 服务同时提供页面和接口，不需要启动 Next.js。

## 打开与配置

在项目目录的 PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe run_chatbox.py
```

打开 <http://127.0.0.1:8001>。保持启动窗口运行；按 Ctrl+C 停止。
也可以运行 `./start-chatbox.ps1`，若本机策略禁止脚本，使用上面的 Python 命令即可，无需更改安全策略。

展开页面左侧“API Key 设置”，填写 OpenRouter 与 TypeSafe 两个密码框并保存。页面把密钥写入项目根目录 `.env`，立即应用且不回显。也可以参考 `.env.example`，手动在 `.env` 填写 `OPENROUTER_API_KEY` 与 `TYPESAFE_API_KEY` 后重启。`.env` 已被 Git 忽略。

如果页面已在后台运行，使用 `./start-chatbox.ps1 -Restart` 重启。本脚本只会停止配置端口上、命令行匹配本项目 `run_chatbox.py` 的 Python 进程；其他进程会拒绝停止。若修改了端口，先停止旧端口上的服务再改配置。

模型名称和端口仍在 `config/chatbox.local.json`；密钥只放 `.env`。文字和图片走 OpenRouter Chat Completions；JEV 直连 TypeSafe System One，默认 `jev-latest` 和 `https://api.typesafe.ai/v1/systemone`。Chatbox 只从 `.env` 读取 `OPENROUTER_API_KEY` 与 `TYPESAFE_API_KEY`，不会导入其中的 DeepSeek、Gemini 或其他旧服务密钥，也不会把 Key 返回浏览器。

JEV 参考：[TypeSafe 官方 Agent skill](https://docs.typesafe.ai/agent-skill) 和 [TypeSafe API Key 控制台](https://console.typesafe.ai)。本次只做模拟响应测试，尚未用真实 Key 验证账号权限或实时可用性。

## 使用

- 没有 Key：点“按原文填写引文”，选择判例、法规、书籍或网页，补充原文字段，再点确认组装。
- 配置 Key 后：输入案名、引用号或网页链接，发送。多个搜索匹配会显示候选按钮，点选后继续。
- DOI / ISBN：直接访问 Crossref / Open Library 获取记录，不需要调用生成模型。
- 点击附件按钮或拖入单个文件；支持 PDF、DOCX、PPTX、XLSX、JPG、PNG、WebP，默认最多 50 MB（`config/chatbox.local.json` 的 `max_upload_mb`，上限 200）。程序自动识别类型、摘取字段并按规则生成引文；缺少必需信息时会说明缺了什么。
- JEV 默认只做文档分类对比（shadow），结果不会直接改变引用内容。失败时保留原有处理路径。
- 修改定位引用：改已有表单中的定位字段，再次确认即可生成新结果。

## 当前边界

这是一版本地快速入口，不是完整多轮引用系统：刷新页面丢失对话，候选有效期 30 分钟；尚无持久会话、自动理解“上一条加段落”或版本回滚。手动严格组装目前只有上述四类。

所有最终引文都明确标为“未核验”：数据库匹配不等于逐字段来源审计完成。不会把模型生成的作者、日期或中立引用号直接当作已核验事实。

页面只监听本机 `127.0.0.1`，没有部署到公网。配置后，相关文本或图片会发送给 OpenRouter 及其模型服务商，资料检索会访问外部数据库/网站，请勿上传不允许外发的材料。

`daily_spend_cap_usd` 是估算的进程内阈值，不是账单硬上限：服务重启会重置本地计数，并发/在途请求可能超额。需要严格控制时请同时在 OpenRouter 给 Key 设置限额。

## 测试与初次安装

本机已建立 `.venv`。另一台电脑需要先安装 Python 3.11+，然后：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
```

离线测试（不读取旧 `.env`）：

```powershell
$env:MCGILL_SKIP_DOTENV='1'
$env:HF_SPEND_DATASET=''
$env:HF_TOKEN=''
.\.venv\Scripts\python.exe -m pytest tests/test_chatbox_http.py tests/test_chatbox_providers.py tests/test_jev_decisions.py -q
```

完整离线回归：`.\.venv\Scripts\python.exe scripts/test_chatbox_offline.py`。测试启动器设置虚拟凭据并禁止外部 Python socket；保留 Windows 事件循环使用的本机回环连接。2026-09-18 验收：659 passed、2 skipped；浏览器实测首页、配置展示、字段提交和结果显示，无控制台错误。TypeSafe 官方直连只做模拟响应测试，真实付费接口尚未验收。
