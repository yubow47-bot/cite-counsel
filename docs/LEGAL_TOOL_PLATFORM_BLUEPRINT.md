# 法律工具平台：技术规格

状态：以运行代码为准，本文随代码更新。日期：2026-09-30。工程细节见 `HARNESS.md`。

## 1. 定位

Cite Counsel 是一个用于法律研究与写作的本地 agent 工作区。用户直接和它对话；模型理解目标，自己决定读取哪条方法说明（Skill）、加载哪些插件、调用哪些工具、按什么顺序组合、何时停止。能力通过插件和 Skill 扩展，未来持续加入更多法律基础工具。

模型可以阅读材料、比较来源、做推理和起草，但必须说明证据和不确定性。平台不裁判模型的法律判断；它保证的是另一件事：**来源身份不能被模型伪造**。凡是有对错、能由代码确定的部分，交给确定性工具：引用格式（McGill Guide）、引语对原文的逐字核对、日期算术。

### 1.1 和“LLM + 数据库 MCP”的区别

单个数据源插件与该数据源自己的 MCP 服务没有本质区别。单纯“搜判例”这件事，我们不比 A2AJ 强。平台的价值在三处：

1. **“已核验”由推导链决定，不由模型决定。** 数据源返回带来源的记录，模型拿到编号和摘要；引文由格式化插件从记录字段生成；一条成品只有在推导链的每个叶子都来自数据库并带 `source_id` 时才算已核验。
2. **跨来源的来源追溯。** 判例、法规、议案、书、文章、网页、文件、用户补充和模型填写的字段在同一套契约下流转，来源标签一路传到最终成品，例如一整份参考文献。
3. **组合。** 同一次对话里，检索、选择、阅读、格式化、核对原文、汇总参考文献可以连成一条链，每一步都留有记录。

### 1.2 渊源

- **数据来源追溯（data provenance）**：where-provenance、W3C PROV。
- **污点追踪（taint tracking）**：字段带来源标签，标签沿推导链传播；不能被证据支撑的来源一律降为 `model`。
- **法律引文治理系统**：KeyCite、Shepard's 的信号必须有真实数据支撑。
- **RAG grounding**：“已核验”是硬闸门；回复里的事实标注是软提示，只标注、不隐藏。
- **Harvey LAB 的公开架构**：借鉴“材料必须能被 agent 读取、循环保持通用”；本产品有在线来源与引文记录，因而保留专业检索和格式工具。

### 1.3 非目标

- **不裁判法律结论。** 可追溯、格式正确、文本匹配都不等于法律正确或可适用；平台不给模型的法律判断打“已核验”。
- **不隐藏模型的回复。** 回复检查只做逐事实标注（§5.4）。
- **不做沙箱。** 插件是已安装的可信代码；契约只拦截类别越权和来源伪造。
- **不引入外部 agent 框架，不热加载插件。**
- **暂不做 `case.treatment`（判例后续处理）。** 将来做必须用 `Finding.coverage = "partial"` 表达覆盖不全。

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ 界面：对话框 + 设置栏（模型、API Key、插件开关与设置）          │
│       插件可注册自己的结果渲染、装饰器和右侧面板                │
├──────────────────────────────────────────────────────────────┤
│ Harness（领域无关，不可关闭）                                  │
│   · 单一流式 agent 循环：Skill 目录、渐进加载插件、原生工具调用  │
│   · 内置工具：load_plugin · read_skill · record__read ·        │
│               record__compose                                 │
│   · 记录存储：会话内保存记录 / 成品 / 结论，按编号引用           │
│   · 契约检查：插件类别 → 可产出的来源；推导链叶子审计            │
│   · 回复标注：模型文字里的事实标出会话内出处或 unsourced         │
│   · 会话持久化、事件日志、网页来源快照                          │
├──────────────────────────────────────────────────────────────┤
│ Skill（Markdown 方法说明，按需读取）                           │
│   citation_scope · source_review                              │
├──────────────────────────────────────────────────────────────┤
│ 插件（可勾选）                                                │
│   数据源：a2aj · legisinfo · crossref · openlibrary            │
│   提取：  file · web                                          │
│   功能：  mcgill · quote · bibliography · deadlines            │
├──────────────────────────────────────────────────────────────┤
│ 连接器与规则库：local_tools/*_api.py · mcgill_rules.json       │
├──────────────────────────────────────────────────────────────┤
│ 外部服务：OpenRouter · A2AJ · LEGISinfo · Crossref · …         │
└──────────────────────────────────────────────────────────────┘
```

插件之间互不认识，只认契约和记录编号；需要另一插件能力时，通过 `requires` 声明并调用其公开 `api`。Harness 不懂法律。当前唯一的领域耦合：McGill 专属的 `record__compose`（实现在 `plugins/mcgill/compose.py`）仍由核心注册为内置工具。

## 3. 契约层

契约层由 harness 持有，数据结构在 `core/tool_contracts.py`，检查在 `harness/records.py`。

### 3.1 数据结构

```python
Field(value, origin, source_id=None, rule_id=None, span=None, derivation=None)
    # origin ∈ {"database", "extracted", "user", "computed", "model"}，闭集

Record(source_type, fields: dict[str, Field], provider, record_id)
    # source_type：jurisprudence / legislation / bill / book / journal_article /
    #              website / news_online / treaty / foreign / by_law / ...

Artifact(kind, content, derivation)        # 引文、定位引用、参考文献、期限日期……
Finding(verdict, detail, coverage, derivation)
    # verdict ∈ {confirmed, contradicted, inconclusive}；coverage ∈ {complete, partial}

Derivation(inputs: tuple[Field | Derivation, ...], rule_id, input_names)
```

五种来源：

| 来源 | 含义 |
|---|---|
| `database` | 数据库返回，必须带 `source_id`（如 `a2aj:2022 SCC 39`、`legisinfo:45-1/C-63`，判决全文用其 URL） |
| `extracted` | 从文件或网页读出 |
| `user` | 能在用户本会话原话里找到 |
| `computed` | 由代码推导，必须带推导链，叶子为其他来源 |
| `model` | 模型写入，会话里没有可核对的出处 |

### 3.2 谁能产出什么

| 产出方 | 输出 | 字段来源 |
|---|---|---|
| 数据源插件 | Record | 只能是 `database` |
| 提取插件 | Record | 只能是 `extracted` |
| 功能插件 | Artifact / Finding | 推导链叶子须经审计（§3.3） |
| 内置 `record__compose` | Record | 按引证结果继承 `database` / `extracted` / `user`，否则 `model` |

`Context.save` 是插件写入证据的唯一入口：记录类别越权抛 `ContractError`；成品和结论的推导链逐叶子审计。内置工具以 `harness` 身份写入，不受类别检查。

### 3.3 叶子审计与可信度推导

- `database` / `extracted` 叶子必须由本会话已存的证据支撑：值与来源完全一致，或包含在同一 `source_id` 的更长文本里。支撑不了的叶子**不拒绝，降为 `model`**。
- `user` 叶子必须是 harness 本次调用核对过的用户原话切片。
- `computed` 叶子没有推导链、推导图无法审计时抛 `ContractError`。
- `model` 叶子不是证据，不进入证据库，因此复制的复制无法洗成有来源。
- **已核验**：推导链上每个叶子都是 `database` 且带 `source_id`（`is_grounded`）。任一 `user`、`extracted`、`model` 叶子即为未核验。
- 功能插件在去掉部分字段时重新推导，例如参考文献不含定位引用，所以脚注里用户填写的定位引用不影响参考文献条目的核验状态。
- 可追溯不等于正确，也不等于适用。

### 3.4 引用、阅读与用户原话

- 工具产出的 Record / Artifact / Finding 由 harness 编号（`rec_1`、`art_2`、`fnd_3`）。模型收到编号和摘要，长字段在摘要里截到 300 字符。
- 功能工具只接受编号，从存储里取原对象；模型无法在调用途中改写值。记录被新版本取代（`supersedes`）后，旧编号解析到最新版本。
- 模型需要原文时用 `record__read(ref, field, offset, limit)` 分段读取文本字段，单次最多 4,000 字符，返回片段、总长、下一偏移和该字段的来源。阅读不改变任何字段的来源。
- 需要用户原话的参数声明为 `UserText`：在用户消息里找得到时，harness 替换为原话切片；找不到时原样放行，由插件用 `Context.provenance` 记录真实来源（会话记录 → 用户原话 → `model`），**标注而不拒绝**。检索词不受此限。
- 普通参数只有在也出现在用户原话里时才能支撑 `user` 来源；调用过工具不会让模型参数升级为用户输入。

### 3.5 模型自建记录：`record__compose`

数据库找不到、或需要补字段时，模型用 `record__compose(record_type, fields, base_ref?)` 自建或更新记录。每个字段是 `{name, value, source, quote}`：

- `source` 为记录编号时，`quote` 必须出现在该记录的某个非 `model` 字段里，`value` 必须出现在 `quote` 里（比较时忽略空白和大小写），字段继承该字段的来源和 `source_id`。
- `source` 为 `"user"` 时，引文必须出现在用户原话里，字段为 `user`。
- 其余情况照样写入，标为 `model`，并说明原因。
- 字段形状不合法（名称不属于该类型、过长、格式不符）不写入，立即报告原因。
- 判例、法规、议案等权威类型的记录若没有任何 `database` 或 `user` 字段，结果附警告：记录的来源可能只是描述该作品，而不是作品本身。
- 不要求先做网络检索。

## 4. 插件与 Skill

### 4.1 插件清单

```python
Plugin(
    name, title, category,       # category ∈ {"source", "extract", "function"}
    description,                 # 一句话，加载前模型只看到这一句
    tools,                       # 名称、描述、pydantic 参数、handler(ctx, params) -> Result
    actions,                     # 界面按钮直接触发的确定性操作，不经过模型
    ui, settings, requires, api,
    fact_patterns,               # 本领域的事实形状，供回复标注（§5.4）
    skill,                       # 可选 Markdown 方法说明，与工具分开读取
    default_enabled,
)
```

`Result(content, blocks, final)`：`content` 给模型，`blocks` 给界面，`final` 表示产物已可交给用户，不切断模型后续的工具调用。内置插件放在 `plugins/`；第三方插件通过 entry point `citecounsel.plugins` 在启动时加载。

### 4.2 插件目录

| 插件 | 类别 | 默认 | 工具 | 底层 |
|---|---|---|---|---|
| `a2aj` | 数据源 | 开 | `find_case`、`find_legislation`、`full_text`（判决全文作为 `database` 字段加到新版本记录） | `local_tools/a2aj_api.py`、`core/source_tools.py` |
| `legisinfo` | 数据源 | 开 | `bill`（按编号）、`bills`（按关键词，最新优先） | `local_tools/legisinfo_api.py` |
| `crossref` | 数据源 | 开 | `doi`、`article` | `local_tools/crossref_api.py`、`core/bibliographic.py` |
| `openlibrary` | 数据源 | 开 | `isbn`、`book` | `local_tools/openlibrary_api.py`、`core/bibliographic.py` |
| `file` | 提取 | 开 | `extract`（PDF、DOCX、PPTX、XLSX、图片） | `local_tools/file_extractor.py` |
| `web` | 提取 | 关 | `search`（Exa 或 DuckDuckGo Lite，由用户在设置里选）、`fetch`（SSRF 防护，保存原始响应快照） | `plugins/web`、`local_tools/web_extract.py` |
| `mcgill` | 功能 | 开 | `cite`（记录 → 引文，`pinpoint` 为 `UserText`，或 `pinpoint_from` 取 quote 的定位产物）、`missing` | `core/mcgill_format.py`、`mcgill_rules.json` |
| `quote` | 功能 | 开 | `check`（引语对判决全文逐字核对，产出 Finding 和定位引用 Artifact） | `core/quote_check.py` |
| `bibliography` | 功能 | 开 | `build`（引文成品 → 分节排序、去定位引用、逐条重算核验状态） | `core/bibliography.py` |
| `deadlines` | 功能 | 关 | `compute`（输入全部回显） | `core/legal_tools.py` |

Harness 内置、无需加载的工具：`read_skill`、`record__read`、`record__compose`；尚有未加载插件时另有 `load_plugin`。

### 4.3 Skill

- 来源：`skills/*.md`、插件目录下的 `SKILL.md`、插件显式声明的 `skill` 路径。
- 格式：首行 `# 标题`，第二行 `> 一句话说明`，正文不超过 4,000 字符。
- 系统提示只列名称和一句说明；`read_skill(name)` 返回正文。插件所属 Skill 只在插件启用时可读，读取它不加载工具；`load_plugin` 只暴露工具 Schema，不注入说明。Skill 与工具不要求一一对应。
- 现有：`citation_scope`（引用范围与定位引用的来源）、`source_review`（来源身份与覆盖范围的比对）。

### 4.4 设计要求

- **一个插件只做一件事。** 数据源不做格式化，格式化不查数据库。
- **数据源诚实表达覆盖范围。** 结果为空时说明查了哪里，不说“不存在”。
- **功能插件校验输入类型**，不支持的记录类型明确拒绝并说明原因。
- **工具只报告发生了什么，不替模型安排流程。** 插件说明写输入含义、输出语义和能力限制；不写“接下来调用哪个工具”。
- **插件可以自带界面**：`registerBlock`、`decorate`、`on`、`registerPanel`、`action`、`openSource`；没有界面的插件用通用卡片。

## 5. Agent 循环

### 5.1 执行

1. 每一步重建系统提示：通用执行原则（以用户实际目标为准；事实要有实际取得的材料支撑；区分来源陈述、用户输入、推断与假设；检查结果是否对应目标和版本；按错误原因自行决定下一步；说明未解决的部分），加上待加载 / 已加载 / 已关闭插件的一句话说明、可用 Skill 目录和回复语言。
2. 模型可直接回答，或调用 `read_skill`、`load_plugin`、工具。两者都不是必经步骤。
3. Harness 校验参数、执行、做契约检查、保存记录；`content` 进入对话历史（单条上限 6,000 字符），`blocks` 交给界面。
4. 预算：每轮最多 20 个工具步骤；只加载插件的步骤不计，但总迭代数以“20 + 插件数”为界。预算用尽时给出提示，再给模型一次不带工具的收尾调用。
5. 流式与非流式共用同一个生成器 `_turn_events`，没有第二份循环。

启用插件只表示“可以被选用”，不会运行任何东西。

### 5.2 错误

- Harness 产生的错误带 `kind` 和 `retryable`（如 `invalid_arguments`、`unavailable_tool`、`contract_violation`、`source_contract`、`unknown_error`）。插件返回的普通错误保留原内容，补充 `tool_reported_error` 与 `retryable: null`。
- 同一轮里 `retryable: false` 的失败调用若以相同参数再次发出，不再执行，直接返回原错误；任一调用成功后清空这一记录。
- 空结果不等同于异常；未知异常报告 `unknown_error`，不猜测为网络故障。

### 5.3 模型

- OpenRouter 兼容接口；设置栏选择对话模型，配置文件可另设图像提取模型。
- `ModelProfile` 按模型前缀控制 `<think>` 标签与文本形式工具调用的兼容处理；核心循环不检查模型名称。
- 对话历史按整轮截取：当前轮始终完整保留，更早的整轮在约 40 条消息的软预算内保留。

### 5.4 回复标注（harness 层，始终生效）

- 事实形状 = 通用形状（年份、中立引用、报告引用、段落 / 条文定位、法规章号、DOI）+ 所有已启用插件的 `fact_patterns`，不论本轮是否加载。
- 证据索引只含非 `model` 来源的记录字段和用户原话，不含工具 JSON 和界面操作记录。
- 找得到的事实标出记录编号、来源、摘录，以及是否有来源快照；找不到的标为 `unsourced`。**文字从不隐藏。**
- 标注是文本匹配提示，不证明事实正确、来源权威或法律适用。

## 6. 记录、会话与留痕

- **会话持久化**：每会话一个 JSON 快照（消息、已加载插件、已读 Skill、插件状态、全部编号对象、附件索引），原子写入；重启后凭 token 哈希恢复，编号续排。闲置 4 小时过期，启动时清扫并限制总数。页面加载会开启新对话并删除旧会话。
- **事件日志**：`<session_id>.events.jsonl` 追加每一步的模型请求（系统提示、历史、工具 Schema、模型名称）、模型返回、工具调用和工具结果。用于追查当时给了模型什么；不保证重放得到相同输出，也不是防篡改证明。
- **来源快照**：网页 `fetch` 成功时保存响应字节、请求与最终 URL、取回时间、SHA-256、HTTP 状态和部分响应头，关联到记录。用户可从网页卡片或带快照的事实标注打开，接口校验会话令牌和哈希，以纯文本展示。`version_as_of` 暂为空，取回时间不能代替法条版本日期。其他来源插件尚无快照。
- 附件、事件日志和快照随会话过期或删除一并清理。

## 7. 界面与本地服务

- **对话框**：用户消息、模型思考过程（流式）、回复及事实标注、插件结果卡片、“调用了哪个插件的哪个工具”的活动行。
- **设置栏**：模型 ID；OpenRouter API Key（写入项目 `.env`，只显示是否已设置）；插件按类别显示开关、说明、工具列表和各自设置。
- **右侧面板**：只在有插件注册面板时出现。
- **本地服务**：只监听 `127.0.0.1`；TrustedHost、同源检查、限流、CSP；上传按扩展名、大小（默认 50 MB）和文件头检查。每日花费上限是进程内估算，不是计费硬限制。

## 8. MCP

两个方向，都以契约为边界，均未实现：

- **外部 MCP 服务当插件用。** 适配层能给出稳定记录标识并确认数据来自该数据源时，字段才能标为 `database`；否则标为 `extracted`。A2AJ 的 MCP 是第一个候选。
- **把插件暴露为 MCP 服务**，输出带字段来源和推导链。

## 9. 实施状态

| 部分 | 位置 | 状态 |
|---|---|---|
| 单一流式循环、渐进加载、错误语义、预算 | `harness/core.py` | 可用 |
| Skill 目录与 `read_skill` | `harness/skills.py`、`skills/` | 可用 |
| 记录存储、类别检查、叶子审计 | `harness/records.py`、`core/tool_contracts.py` | 可用；本次修复见 §11 |
| `record__read`、`record__compose` | `harness/core.py`、`plugins/mcgill/compose.py` | 可用；compose 仍由核心注册 |
| 回复标注 | `harness/grounding.py` | 可用 |
| 会话持久化、事件日志、网页快照 | `harness/session.py`、`plugins/web` | 可用；其他来源无快照 |
| 数据源、提取、功能插件 | `plugins/` | 见 §4.2 |
| 宪法性文件的固定引用形式 | `mcgill_rules.json`、`core/mcgill_format.py` | 可用；支持直接传法律名称（§11） |
| MCP | — | 未做 |
| 真实模型上的验收（§10） | — | 未做 |

## 10. 验收场景

以下场景须在设置栏实际选用的模型上通过：

| 输入 | 期望 |
|---|---|
| `[1999] 1 SCR 688` | `a2aj` → `mcgill`：`*R v Gladue*, [1999] 1 SCR 688.` 已核验 |
| `gladue` → `我要的是最有名的那个` | 候选中含 SCC 1999；第二句选中它，已核验 |
| `加拿大最近限制未成年人使用社交媒体的 bill` | `legisinfo` 按关键词检索；回复中记录之外的议案编号标为 `unsourced` |
| `我要引用 Miranda rights 那个美国本身的 case` | 说明没有启用的美国判例数据源；`web` 已安装但关闭时，提示可在设置中打开；不编造引用 |
| `Hart The Concept of Law` | `openlibrary` 候选 → `mcgill` 书籍引文；缺出版地时经 `record__compose`（`source: "user"`）补充，结果未核验 |
| `Edwards v Canada AG 1929` → `是判例，Privy Council，[1930] AC 124` | 数据源查不到 → `record__compose` 以用户原话建记录 → `*Edwards v Canada (AG)*, [1930] AC 124 (PC).` 未核验 |
| `2022 SCC 39` → 核对一句引语 → 加上段落号 | `a2aj.full_text` → `quote.check` 定位段落 → `mcgill.cite(pinpoint_from=…)`，仍然已核验 |
| 用户没给定位引用，模型自行填入 | 引文照常生成，标为未核验，卡片和工具结果都说明定位引用由助手添加 |
| 长判决的某段内容 | 模型用 `record__read` 分段读取；回复中的事实标出记录出处 |
| 生成参考文献 | 分节、排序、去定位引用；每一条的核验状态由推导链重算 |

契约测试：

- 越权产出来源类型的插件被拒绝；
- 不存在或其他会话的编号被拒绝；
- 模型编造的 `database` 叶子降为 `model`，成品未核验；
- `UserText` 参数传入用户没说过的值时，按真实来源标注；
- 模型回复中无出处的事实标为 `unsourced`，文字不隐藏；
- Skill 读取与插件加载互不触发。

## 11. 本次修复（2026-09-30）

1. **错误引文的数据库来源继承。** 文本匹配保留词和数字边界，全文片段只保留为提取证据。Compose 仅在完整复制同类型记录的对应字段时保留数据库来源，混用不同来源身份会降级。模型直接填写的定位引用按用户原话或模型输入标记；quote 定位产物绑定作品身份。截断引用、混拼案件字段和助手自填定位引用的回归测试已通过。
2. **宪法性文件引用。** `constitutional` Schema 已补齐，`fixed_form()` 已接入 `mcgill__cite(title=…)`。Charter 等固定形式可直接按名称生成；格式检查与法条文本核验分别标记。相关回归测试已通过。

## 12. 待定

1. **A2AJ 数据源用自己的连接器，还是接 A2AJ 的 MCP。** 取决于 A2AJ MCP 能否给出稳定的记录标识。
2. **引文版本的界面表达。** 记录新版本（`supersedes`）是否需要在界面上显示历史和撤销。
3. **法条版本日期。** `version_as_of` 的数据来源，以及数据源插件的原始响应快照。
4. **`record__compose` 的注册耦合。** 是否改由 `mcgill` 插件注册。

## 13. 已定决策

| 问题 | 决定 |
|---|---|
| 定位 | 法律研究与写作工作区；模型可阅读、推理、起草，已核验只由推导链给出 |
| 插件粒度 | 一个数据源或一项功能一个插件 |
| 方法说明 | 独立 Markdown Skill，按需读取，不随插件加载注入 |
| 路由 | 完全由模型决定；harness 不做输入提示或路由 |
| 插件间数据传递 | 通过记录存储和编号；插件依赖须在 `requires` 中声明 |
| 不合格来源 | 标注降级为 `model`，不拒绝调用 |
| 回复检查 | harness 层，对所有回合生效，只标注不隐藏 |
| 工具可见性 | 渐进加载；已关闭的插件也列出，并注明已关闭 |
| 会话持久化 | 本地 JSON 快照 + 事件日志 + 会话目录 |
| 热加载 | 不做；启动时发现插件 |
