# Cite Counsel

Cite Counsel 是一个实验性的法律引用工具，目标是按 *Canadian Guide to Uniform Legal Citation*（McGill Guide，第 10 版）生成引文，并且**让每一个事实都能追溯到来源**。

> 这是研究辅助，不是引文正确的保证。使用前请对照原始来源和官方 McGill Guide 逐条核对。

界面是 **Chatbox（harness）**：`python run_chatbox.py`，单个 Python 服务，默认 <http://127.0.0.1:8001>，对话式 agent + 插件，持续开发中。

底层能力（数据库适配器、文件提取、McGill 格式化）在 `core/`、`local_tools/`、`llm_api/` 里。

---

## 设计核心：模型不经手事实

多数“LLM + 搜索”应用把来源当作软提示。Cite Counsel 把它做成硬约束：

1. **模型只拿到编号。** 数据源返回带来源的记录（`rec_N`），模型看到的只有编号和摘要；对象留在会话存储里，后续工具凭编号取回原件，模型无法改写后再传入。
2. **“已核验”由推导链计算，不由任何人声明。** 引文由格式化插件从记录字段渲染，每个用到的字段都是推导链上的一片叶子。只有所有叶子都来自数据库（`database`）且带 `source_id`，成品才算已核验；只要有一个 `user` / `extracted` / `model` 叶子，就如实标为未核验。
3. **回复检查只标注，不隐藏。** 模型每段文字里的年份、判例引用、DOI、定位引用等事实，都会对着会话证据逐条匹配：匹配到就附来源和原文片段，匹配不到就标 `unsourced`，文字本身照常显示。

来源分五种：`database`（数据库返回）、`extracted`（从文件/网页提取）、`user`（用户原话）、`computed`（推导计算）、`model`（模型自己写的，本会话无处可查）。

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

### 插件类别与契约

插件分三类，`Store.check_output` 在每次入库时检查，越权即拒绝：

| 类别 | 只能产出 | 例子 |
| --- | --- | --- |
| `source` | 全部字段为 `database` 的 Record（须带真实 `source_id`） | a2aj、legisinfo、crossref、openlibrary |
| `extract` | 全部字段为 `extracted` 的 Record | file、web |
| `function` | Artifact / Finding（由推导链算核验状态） | mcgill、quote、bibliography、deadlines |

保存 Artifact 时还会做**叶子来源对账**：`database` 叶子必须对上本会话证据库里的同 `(value, source_id)` 条目，所以功能插件无法伪造一个“已核验”的叶子。模型自己填的值不入证据库，永远洗不成有来源。

模型需要自己组装记录时用内置工具 `record__compose`：每个字段给出 `{value, source, quote}`，harness 核对原话确实在那条来源里、值确实在原话里，对上了才继承来源；对不上照常写入但标 `model`。字段格式不对（超长、名称不属于该类型、形状不对）的不写入并当场说明。

**刻意的边界：** 插件是已安装的可信代码，不是沙箱；契约拦截的是类别误用和伪造出处。详见 [docs/HARNESS.md](docs/HARNESS.md)。

### 内置插件

| 插件 | 类别 | 默认 | 工具 | 数据源 / 实现 |
| --- | --- | --- | --- | --- |
| `a2aj` | source | 开 | `find_case` `find_legislation` `full_text` | A2AJ 判例与法规 |
| `legisinfo` | source | 开 | `bill` `bills` | 联邦议案（LEGISinfo） |
| `crossref` | source | 开 | `doi` `article` | Crossref |
| `openlibrary` | source | 开 | `isbn` `book` | Open Library |
| `file` | extract | 开 | `extract` | PDF / DOCX / PPTX / XLSX / 图片提取 |
| `web` | extract | 关 | `search` `fetch` | Exa 搜索（或 DuckDuckGo Lite）；页面抓取带 SSRF 防护 |
| `mcgill` | function | 开 | `cite` `missing` | 按 `mcgill_rules.json` 渲染引文，列出缺失字段 |
| `quote` | function | 开 | `check` | 对已存判决全文核对引语并定位段落 |
| `bibliography` | function | 开 | `build` | 由已存引文生成参考文献，重新推导核验状态 |
| `deadlines` | function | 关 | `compute` | 期限日期计算，输入全部回显 |

新增插件：在 `plugins/<name>/__init__.py` 里导出 `PLUGIN`（`harness.plugin.Plugin`），或通过 `citecounsel.plugins` entry point 发布。启动时发现，不热加载。

### 会话与持久化

每个会话一个文件 `sessions/<id>.json`（附件在 `sessions/<id>/attachments/`），写盘为原子操作，重启后编号续排、引用仍然有效；`token` 只存哈希；空闲 4 小时过期，启动时清扫过期文件并限制总量。

## 快速开始（Chatbox）

需要 Python 3.11+。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_chatbox.py
```

打开 <http://127.0.0.1:8001>。也可运行 `./start-chatbox.ps1`（`-Restart` 重启，只会停止匹配本项目的进程）。

- **设置栏**：模型 ID、OpenRouter API Key（写入根目录 `.env`，立即生效、不回显）、插件开关及各插件自己的设置项。启用只表示模型“可以选用”，不会自动运行。
- **配置**：复制 `config/chatbox.example.json` 为 `config/chatbox.local.json` 可改端口、模型、上传上限（默认 50 MB）、每日花费阈值。密钥只放 `.env`（参考 `.env.example`）。
- **网络搜索**：在设置栏选择搜索服务；Exa 需要 `EXA_API_KEY`（设置栏的值优先于 `.env`）。新建记录时若启用了 web 插件，要求本会话先发起过一次 `web__search`。

### 边界与隐私

- 只监听 `127.0.0.1`，带 TrustedHost、同源检查、限流和 CSP。
- 配置 Key 后，对话内容会发送给 OpenRouter 及其模型服务商；插件会访问外部数据库和网站。请勿提交不允许外发的材料。
- `daily_spend_cap_usd` 是进程内估算阈值，不是账单硬上限；严格控制请在 OpenRouter 给 Key 设额度。
- 提取与搜索结果依赖上游覆盖；A2AJ 命中只核验来源元数据，不核验最终格式。

## 目录

```text
harness/        agent 循环、插件接口、记录存储、会话持久化、回复检查、本机 HTTP 面与页面
plugins/        内置插件（a2aj、legisinfo、crossref、openlibrary、file、web、mcgill、quote、bibliography、deadlines）
core/           契约数据结构（tool_contracts）、McGill 格式化与规则、引语核对、参考文献、花费统计
local_tools/    数据库适配器、URL 防护、文件提取
llm_api/        Gemini 与 OpenAI 兼容客户端
mcgill_rules.json  McGill 规则与模板（格式化的唯一来源）
docs/           HARNESS.md（工程架构）、LEGAL_TOOL_PLATFORM_BLUEPRINT.md（设计规格）、CHATBOX_QUICKSTART.md
tests/          后端单元与契约测试
scripts/        离线回归与可选的线上探测脚本
profiling/      可能调用付费 API 的诊断脚本（pytest 不收集）
```

## 测试

```powershell
python -m pytest                                   # 后端，仅收集 tests/
.\.venv\Scripts\python.exe scripts/test_chatbox_offline.py   # Chatbox 离线回归（虚拟凭据，禁外网）
```

契约测试覆盖：越权产出来源、跨会话/不存在的编号、伪造 database 叶子、模型值不入证据库、`record__compose` 的证据核对与格式检查、持久化往返（`tests/test_harness.py`、`tests/test_tool_contracts.py`、`tests/test_persistence.py` 等）。多数测试 mock 了外部服务，通过不代表上游 API、凭据或模型此刻可用；`scripts/` 与 `profiling/` 下的脚本会真实调用外部服务，可能产生费用。

## 已知未做

- CanLII 插件（连接器在 `local_tools/canlii_api.py`，尚未包装成插件）。
- MCP 集成（外部 MCP 当插件、插件暴露为 MCP）。
- 界面对回复事实标注（链接、片段、高亮、“无来源”醒目呈现）的完整展示。

## 许可与声明

本项目不包含也不替代 McGill Guide；该指南是独立的受版权保护出版物，是其规则的权威来源。代码以 [MIT License](LICENSE) 发布。
