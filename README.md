# Cite Counsel Harness

一个本机运行的法律引用 agent harness。你和它对话，模型自己决定加载哪些插件、调用哪些工具；harness 保证模型写出的每个事实都能追溯到来源。首个应用领域是按 *Canadian Guide to Uniform Legal Citation*（McGill Guide，第 10 版）生成引文。

> 实验性研究辅助，不是引文正确的保证。使用前请对照原始来源和官方 McGill Guide 逐条核对。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_chatbox.py      # 打开 http://127.0.0.1:8001
```

一个 Python 进程同时提供页面和接口。

## 它解决什么问题

“LLM + 搜索”类工具通常把来源当作软提示：模型读了资料，再用自己的话写出引文，用户无从知道哪个字段是查到的、哪个是编的。harness 用三条规则约束这件事：

1. 模型不经手事实。数据源返回带来源的记录（`rec_N`），模型只看到编号和摘要；对象留在会话存储里，工具凭编号取回原件，模型无法改写后再传入。
2. “已核验”由推导链计算，不由任何人声明。引文从记录字段渲染，每个字段是推导链上的一片叶子。所有叶子都来自数据库（`database`）且带 `source_id` 才算已核验；只要有一个 `user` / `extracted` / `model` 叶子，就如实标为未核验。
3. 回复检查只标注，不隐藏。模型每段文字里的年份、判例引用、DOI、定位引用等，都对着会话证据逐条匹配：匹配到附来源和原文片段，匹配不到标 `unsourced`，文字照常显示。

来源分五种：`database`（数据库返回）、`extracted`（从文件/网页提取）、`user`（用户原话）、`computed`（推导计算）、`model`（模型自己写的，本会话无处可查）。

合同审查和法律问答这类判断无法核验，项目不做。范围限于有对错、能由代码验证的工作：引文格式、来源核验、引语对原文、期限计算。

## 架构

```text
用户消息 + 附件
      │
      ▼
 run_turn：系统提示（插件目录 + 已关闭清单 + 输入提示）
      │
      ▼
 模型决定 load_plugin / 调工具（每轮最多 20 步）
      │
      ▼
 _execute：参数校验 ──► handler(ctx, params)
                          ├─ ctx.records.get(ref)   按编号取记录
                          ├─ ctx.save(obj, meta)    入库（先过类别契约）
                          └─ Result(content=编号+摘要, blocks=界面卡片)
      │
      ▼
 content 进消息历史；blocks 交给界面
      │
      ▼
 回复检查：逐事实标注来源
```

| 模块 | 职责 |
| --- | --- |
| `harness/core.py` | agent 循环、工具执行、参数核验、内置 `record__compose` |
| `harness/plugin.py` | 插件接口与发现：`Plugin` / `Tool` / `Result` / `Setting` / `UserText` |
| `harness/records.py` | 会话记录存储：编号、类别契约检查、叶子来源对账 |
| `harness/session.py` | 会话、消息史、附件、本地持久化 |
| `harness/grounding.py` | 回复事实提取与来源标注 |
| `harness/llm.py` | OpenRouter 兼容调用，原生工具调用，支持流式 |
| `harness/app.py` | 本机 HTTP 面、上传检查、设置接口；`harness/rate_limiter.py` 限流 |
| `harness/web/` | 单页界面：对话框、设置栏、插件 UI 加载器 |
| `core/tool_contracts.py` | 契约数据结构：`Field` / `Record` / `Artifact` / `Finding` / `Derivation` |

### 插件类别与契约

插件分三类，`Store.check_output` 在每次入库时检查，越权即抛 `ContractError`：

| 类别 | 只能产出 | 例子 |
| --- | --- | --- |
| `source` | 全部字段为 `database` 的 Record（须带真实 `source_id`） | a2aj、legisinfo、crossref、openlibrary |
| `extract` | 全部字段为 `extracted` 的 Record | file、web |
| `function` | Artifact / Finding（核验状态由推导链算出） | mcgill、quote、bibliography、deadlines |

保存 Artifact 时做叶子来源对账：`database` 叶子必须对上本会话证据库里同 `(value, source_id)` 的条目，功能插件伪造不了“已核验”。模型自己填的值不入证据库，洗不成有来源。

模型自行组装记录用内置工具 `record__compose`：每个字段给出 `{value, source, quote}`，harness 核对原话确实在那条来源里、值确实在原话里，对上才继承来源；对不上照常写入但标 `model`。格式不对（超长、字段名不属于该类型、形状不对）的字段不写入并当场说明。

边界：插件是已安装的可信代码，不在沙箱里运行，契约只拦截类别误用和伪造出处。详见 [docs/HARNESS.md](docs/HARNESS.md)。

### 内置插件

| 插件 | 类别 | 默认 | 工具 | 数据源 / 实现 |
| --- | --- | --- | --- | --- |
| `a2aj` | source | 开 | `find_case` `find_legislation` `full_text` | A2AJ 判例与法规 |
| `legisinfo` | source | 开 | `bill` `bills` | 联邦议案（LEGISinfo） |
| `crossref` | source | 开 | `doi` `article` | Crossref |
| `openlibrary` | source | 开 | `isbn` `book` | Open Library |
| `file` | extract | 开 | `extract` | PDF / DOCX / PPTX / XLSX / 图片 |
| `web` | extract | 关 | `search` `fetch` | Exa 搜索（或 DuckDuckGo Lite）；页面抓取带 SSRF 防护 |
| `mcgill` | function | 开 | `cite` `missing` | 按 `mcgill_rules.json` 渲染引文，列出缺失字段 |
| `quote` | function | 开 | `check` | 对已存判决全文核对引语并定位段落 |
| `bibliography` | function | 开 | `build` | 由已存引文生成参考文献，重新推导核验状态 |
| `deadlines` | function | 关 | `compute` | 期限日期计算，输入全部回显 |

### 写一个插件

在 `plugins/<name>/__init__.py` 导出 `PLUGIN`（`harness.plugin.Plugin`），或通过 `citecounsel.plugins` entry point 发布。插件声明：

- `tools`：模型加载插件后可调用的工具（pydantic 参数模型 + `handler(ctx, params) -> Result`）；
- `actions`：插件自己界面上的按钮直接触发的确定性操作，不经过模型；
- `ui`：可选目录，含 `ui.js` / `ui.css`，渲染自己的结果卡片或面板；
- `settings`：显示在设置栏的选项；
- `category`：`source` / `extract` / `function`，决定它能产出什么来源；
- `fact_patterns`：本领域“事实形状”的正则，供回复检查使用。

参数若应是用户原话，声明为 `UserText`，harness 会在执行前换成核验过的原文切片。启动时发现插件，不热加载。

### 会话与持久化

每个会话一个文件 `sessions/<id>.json`（附件在 `sessions/<id>/attachments/`），原子写盘；重启后编号续排、引用仍有效；`token` 只存哈希；空闲 4 小时过期，启动时清扫过期文件并限制总量。

## 使用与配置

- 设置栏：模型 ID、OpenRouter API Key（写入根目录 `.env`，立即生效、不回显）、插件开关及各插件的设置项。启用只表示模型“可以选用”，不会自动运行。
- 配置文件：复制 `config/chatbox.example.json` 为 `config/chatbox.local.json`，可改端口、模型、视觉模型、上传上限（默认 50 MB）、每日花费阈值。密钥只放 `.env`（参考 `.env.example`）。
- 网络搜索：在设置栏选择搜索服务；Exa 需要 `EXA_API_KEY`（设置栏的值优先于 `.env`）。启用 web 插件后，新建记录要求本会话先发起过一次 `web__search`。
- 启动脚本：`./start-chatbox.ps1`；`-Restart` 重启，只会停止命令行匹配本项目 `run_chatbox.py` 的进程。

### 边界与隐私

- 只监听 `127.0.0.1`，带 TrustedHost、同源检查、限流和 CSP；上传按扩展名、大小和文件头检查。
- 配置 Key 后，对话内容会发送给 OpenRouter 及其模型服务商；插件会访问外部数据库和网站。请勿提交不允许外发的材料。
- `daily_spend_cap_usd` 是进程内估算阈值，重启清零，不是账单硬上限；严格控制请在 OpenRouter 给 Key 设额度。
- 数据库命中只核验来源元数据，不核验最终格式；覆盖范围取决于上游服务。

## 目录

```text
harness/        agent 循环、插件接口、记录存储、会话、回复检查、HTTP 面与页面
plugins/        内置插件
core/           契约数据结构、McGill 格式化与规则、引语核对、参考文献、花费统计
local_tools/    数据库适配器、URL 防护、网页读取（web_extract）、文件提取
llm_api/        OpenRouter 客户端与图片视觉提取
mcgill_rules.json   McGill 规则与模板（格式化的唯一来源）
docs/           HARNESS.md（工程架构）、LEGAL_TOOL_PLATFORM_BLUEPRINT.md（设计规格）、CHATBOX_QUICKSTART.md
tests/          单元与契约测试
profiling/      HTTP 调用计时工具
```

## 测试

```powershell
python -m pytest        # 仅收集 tests/
```

契约测试覆盖：越权产出来源、跨会话或不存在的编号、伪造 database 叶子、模型值不入证据库、`record__compose` 的证据核对与格式检查、持久化往返（`tests/test_harness.py`、`tests/test_tool_contracts.py`、`tests/test_persistence.py` 等）。多数测试 mock 了外部服务，通过不代表上游 API、凭据或模型此刻可用。

## 路线

- MCP 集成（外部 MCP 当插件、插件暴露为 MCP）。
- 界面完整展示回复事实标注（来源链接、片段、高亮、“无来源”醒目呈现）。

## 许可与声明

本项目不包含也不替代 McGill Guide；该指南是独立的受版权保护出版物，是其规则的权威来源。代码以 [MIT License](LICENSE) 发布。
