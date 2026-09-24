# Cite Counsel Harness：工程架构

状态：与代码同步（2026-09-23）。设计总纲见 `docs/LEGAL_TOOL_PLATFORM_BLUEPRINT.md`（下称"蓝图"）；
本文写的是 harness 层的工程事实——模块、数据流、契约检查点、以及刻意留下的边界。

## 1. 模块

| 模块 | 职责 |
|---|---|
| `harness/core.py` | Harness 对象：设置、agent 循环、工具执行（UserText 参数核验 + 照实标注）、事实标注、内置 `record__*` 工具 |
| `harness/plugin.py` | 插件接口与发现：`Plugin` / `Tool` / `Result` / `Setting` / `UserText`；启动时扫描 `plugins/` 与 entry point，不热加载 |
| `harness/records.py` | 会话记录存储：编号分配（`rec_N` / `art_N` / `fnd_N`）、摘要、类别契约检查、**叶子来源对账**、值→来源解析（`resolve`）、内置记录工具的数据操作 |
| `harness/session.py` | 会话：消息史、已加载插件、每插件状态、附件、记录存储；**本地持久化**（落盘/复活/清扫）；`Context` 是插件在单次调用里看到的一切（含 `provenance`） |
| `harness/grounding.py` | 回复检查（§5.4）：事实形状匹配、逐事实来源标注（不再隐藏） |
| `harness/llm.py` | 一次 OpenRouter 兼容调用，原生工具调用 |
| `harness/app.py` | HTTP 面：单页 + 设置接口 + 会话接口 + 插件静态资源；本地边界（TrustedHost、限流、CSP） |
| `harness/web/` | 前端：对话框、设置栏、插件 UI 加载器（`registerBlock` / `decorate` / `registerPanel` / `action`） |
| `core/tool_contracts.py` | 契约数据结构：`Field` / `Record` / `Artifact` / `Finding` / `Derivation` 与 `grounding_issues`、`derivation_leaves`、encode/decode |

## 2. 数据流

```
用户消息 ─┐
          ├─► run_turn：系统提示（插件目录 + 已关闭清单 + 输入提示）
附件     ─┘        │
                   ▼
            模型决定 load_plugin / 调工具（最多 6 步）
                   │
                   ▼
            _execute：参数校验 ─► handler(ctx, params)
                   │                 │
                   │                 ├─ ctx.records.get(ref)  解析编号
                   │                 ├─ ctx.save(obj, meta)   入库（先过类别契约）
                   │                 └─ Result(content=refs, blocks=卡片)
                   ▼
            content 进消息历史（6000 字截断）；blocks 交界面
                   │
                   ▼
            回复检查：逐段事实核对（harness 层 + 各启用插件 fact_patterns）
```

要点：**模型不经手证据对象**。工具返回给模型的只有编号加摘要；对象留在会话存储里，
后续工具用编号取回原件。模型无法把一个值改写后再传进去。

## 3. 契约层（唯一不能松动的部分）

- 类别 → 可产出的来源（`harness/records.py: CATEGORY_ORIGIN`）：
  - `source` 插件：只能产出全部字段为 `database` 的 Record（字段须带真实 `source_id`）；
  - `extract` 插件：只能产出全部字段为 `extracted` 的 Record；
  - `function` 插件：只能产出 Artifact / Finding；
  - `user` 来源只能由 harness 内置工具写入，且只接受本会话有出处的值（见 §5）；
    `model` 只存在于契约层与推导叶子中，不再能通过工具写进记录。
- 检查时机：`ctx.save()` 时逐对象检查（`Store.check_output`），越权即抛 `ContractError`，
  工具结果以 `{"error": "contract violation"}` 返回给模型。
- **叶子来源对账**：保存 Artifact / Finding 时，推导链逐叶子审计（`Store._audit`）。
  存储维护本会话的证据库——每条入库字段与推导叶子的 `(origin, value, source_id)`：
  - `database` 叶子必须对上证据库里的同 `(value, source_id)` 条目——function 插件无法
    伪造一个"标着 database"的叶子让成品被算成已核验；叶子值作为子串出现在**同一
    source_id** 的已存长文本里同样算出处（从判决全文/网页正文里读出的事实）；
  - `computed` 叶子必须带推导链；
  - `user` 叶子必须来自本调用经 harness 核验的切片（`UserText` 参数）、本调用的参数值，
    或已在证据库中的字段；
  - `extracted` 叶子必须对上已入库的提取记录；
  - `model` 叶子不拒绝：它就是"模型自己写的、本会话无处可查"的如实标记，
    `grounding_issues` 把含它的产物算作未核验——显示，不信任。
- **证据库不含 model 值**（`Store._register`）：模型填的值不入证据，所以"复制一份再复制"
  永远解析回 `model`，无法洗白成任何来源。
- **UserText 参数**：工具把"应是用户原话"的参数声明为 `harness.plugin.UserText`。
  值真的出自用户消息时，harness 在执行前跑 `user_said`（§3.4），把**核验后的切片**替换进
  参数——插件永远拿不到模型的转述版；不是用户的话时**直接放行**，由 handler 用
  `Context.provenance` 打上真实来源，不再拒绝调用（拒绝的代价曾经全落在用户头上）。
- **值→来源解析**（`Context.provenance` → `Store.resolve`）：一个模型想写进记录的值，
  依次对：已在库记录字段的**精确匹配**（database 优先于 extracted 优先于 user，附来源
  记录编号）→ **子串包含**（值出现在某记录的长字段里——比如从抓回的网页正文里读出的
  事实，继承该记录的来源）→ 用户原话（`user_said` 切片）→ 都不中则**拒绝写入**：模型
  要么从已有记录复制，要么引用用户原话，要么先向用户确认。`model` 仍是合法的
  契约来源（推导叶子层保留），但不再能悄悄写进记录。
- "已核验"不由任何人声明：由推导链计算（`core.tool_contracts.grounding_issues`）。
  推导链叶子全为 `database` 且带 `source_id` 才算核验；一个 `user`/`extracted`/`model`
  叶子就是未核验。

### 刻意留下的边界（不是疏漏）

1. **插件不是沙箱。** 契约检查拦截的是类别误用与伪造出处（早期拦截），插件代码本身按
   已安装的可信代码对待。`database` 叶子的对账堵死了"已核验"造假的路；`user`/`model`
   叶子本来就不产生已核验。规格 §3.2 明示插件可信边界。
2. **user 来源值的校验是字符串级的。** `user_said` 要求值是用户原话的子串（空白归一、
   忽略大小写）。模型转述（"下周五"→"2026-09-25"）会被拒绝——这是设计：转述值在本会话
   拿不到出处，模型要么让用户写明，要么从有出处的记录复制。
3. **编号属于会话。** 存储随会话文件生灭；别的会话的编号查不到，即拒绝。
4. **无沙箱、无签名。** 旧版把引文快照签名后交给客户端保管；现在存储是服务端会话状态、
   编号是唯一句柄，签名机制随之移除（`core/bibliography.py`）。

## 4. Agent 循环（§5）

1. 系统提示列出：已启用未加载插件（供 `load_plugin`）、**已安装但关闭的插件**（注明用户可在设置打开，
   模型不得假装有这个数据源）、已加载插件的说明。
2. 固定格式输入（DOI / ISBN / 中立引用号 / 议案编号）由 harness 识别后**作为提示**附在消息后；
   用哪个插件仍由模型决定，代码不做路由。
3. 工具连续两步全部失败即停止；一步内所有结果都 `final` 时，模型不再带工具地回应一次。
4. 内置工具始终可用，但各有门槛：`record__new`（空记录）在 web 插件启用时要求本会话先
   发起过 `web__search`（`Session.web_search_used`，随会话持久化；一次真实尝试即可，失败
   也算——否则坏 key 会把用户锁死在门外）；`record__add_field` 只接受从已有记录复制或
   用户原话的值，无来源的值拒绝写入（`Context.provenance` 抛 ValueError，走工具失败路径
   返回给模型，并告知该复制哪条记录或去问用户）。

## 5. 回复检查（§5.4）：标注，不隐藏

- 每段模型文字（无论本轮是否加载/调用了插件）都用**所有启用插件**的 `fact_patterns`
  加 harness 内置形状（年份、判例引用、DOI、定位引用）提取事实。
- 每个事实对着会话证据索引匹配（`harness/core.py: _fact_index`）：**记录字段**优先
  （带 `ref`/`field`/`origin`/`source_id`，前端可链接到记录）、其次工具返回内容、
  再次用户原话；匹配到就附**来源加原文片段**（excerpt），匹配不到就标 `unsourced`。
- **没有任何文字被隐藏**：标注数据附在回复文本块上（`{"type": "text", "text", "facts":
  [...]}`），每条含 `fact`/`verdict`/`spans`（原文偏移，便于前端高亮）与来源字段。
  "无来源"如何醒目呈现是前端的职责——后端保证数据在场。
- 模型自己的旧 prose 不在索引里——否则编造的事实会被"说过一遍"洗白成"已出处"。

## 6. 插件清单与类别

| 插件 | 类别 | 工具 | 底层 |
|---|---|---|---|
| `a2aj` | source | find_case / find_legislation / full_text | `core/source_tools.py`、`core/quote_check.judgment` |
| `legisinfo` | source | bill / bills（关键词检索，标题全词匹配、最新在前） | `core/source_tools.py`、LEGISinfo 连接器 |
| `crossref` | source | doi / article | `core/source_tools.py`、`core/bibliographic.py` |
| `openlibrary` | source | isbn / book | 同上 |
| `file` | extract | extract | `local_tools/file_extractor.py`（无模型分类调用） |
| `web` | extract | search / fetch | Exa `/search`（API Key 在插件设置或 `.env` 的 `EXA_API_KEY`，设置栏值优先；支持最近 N 天过滤）；备选 DuckDuckGo Lite（拦截页现在会显式报错，连续空结果会告警）；页面读取走 `llm_api.deepseek_api.extract_from_url`（SSRF 防护 + trafilatura，确定性），抓到的页面连 `date` / `author` / `site_name` 一起存为 extracted 记录；反爬挑战页（Anubis / Cloudflare 等）直接报错拒绝，不入库 |
| `mcgill` | function | cite / missing | `core/mcgill_format.py`、`mcgill_rules.json` |
| `quote` | function | check | `core/quote_check.py` |
| `bibliography` | function | build | `core/bibliography.py` |
| `deadlines` | function | compute | `core/legal_tools.py` |

数据源/提取插件在 handler 内用 `ctx.save()` 入库拿到编号，卡片与模型内容都引用编号；
功能插件同理，产物是 Artifact / Finding。`cite` 的 meta 副载（渲染所用的字段快照）存
在 `Store.meta(ref)`，`bibliography.build` 从那里取条目并重新推导核验状态——所以脚注里
用户填写的定位引用不影响参考文献条目的核验状态（`cite.render.v1` 之外重新推导）。

## 7. 持久化

每个会话一个文件：`<store_dir>/<session_id>.json`，加上 `<store_dir>/<session_id>/attachments/`
里的附件本体。文件内容（`v: 1`）：消息史、已加载插件、每插件状态、附件索引、全部编号对象
（契约对象的 encode/decode 在 `core/tool_contracts.py`）与 meta 副载，`token_hash` 只存哈希。

- **落盘时机**：每轮结束（`run_turn` / `run_action` 的 finally）与附件上传后；写失败只记日志，
  不打断对话。写盘是原子的（临时文件 + `os.replace`）。
- **复活**：`SessionStore.get` 内存未命中时读盘，先验 token 哈希再重建会话——编号从文件里
  续排，引用不会复活成另一个对象。过期判定用墙上时钟（`wall_expires`）。
- **清扫**：Store 初始化时删掉过期会话文件，并把目录压到上限之内。
- 附件写进会话自己的目录（不再是临时目录），重启后引用仍有效。

## 8. 已知边界与后续

- **canlii 插件未做**（规格 §4.2 列出，需 key、默认关）：连接器在 `local_tools/canlii_api.py`，
  等 source_tools 适配后按同一模式包装。
- **MCP（规格 §7）未做**：等契约与首批插件稳定。两个方向（外部 MCP 当插件、插件暴露为 MCP）
  都以契约为边界。
- **契约测试**（规格 §9）：越权产出来源、跨会话/不存在编号、伪造 database 叶子、模型值
  洗白（`Store.resolve` / `_register`）、**无来源值拒绝写入**、**record__new 的搜网门槛**、
  来源标注、持久化往返——见 `tests/test_harness.py`、`tests/test_function_plugins.py`、
  `tests/test_tool_contracts.py`、`tests/test_extract_plugins.py`、`tests/test_persistence.py`、
  `tests/test_web_search.py`。
- **前端展示（未做，等后端数据形态定稿）**：回复事实的 `facts` 标注（链接 + 片段 + 高亮、
  "无来源"醒目呈现）与来源插件的 `record_card` 渲染。