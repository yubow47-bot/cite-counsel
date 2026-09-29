# 法律工具平台：技术规格

状态：设计已定，实现中。日期：2026-09-22。

## 1. 定位

我们不是在做又一个法律 chatbox，也不是在做 Mike（`mikeoss.com/workflows`）那样的高抽象法律工作流平台。Mike 在 prompt 层面做文章："NDA Review"、"Commercial Lease Review" 这类技能，本质是把一份文档喂给模型、让模型做判断。这类工作我们不做：合同风险判断是判断，不是事实，没有办法用数据库核验。

我们做的是**法律工作里可以被验证的基础部分**：引用格式（McGill Guide）、引文对权威源的核验、引语对原文的核对、期限计算。这些问题的答案有对错，对错可以由代码验证。

目标形态：一个真正的 agent，像 `deepseek-ai/deepseek-harness`（dsh）一样"everything is a plugin"。用户直接和它对话；模型理解输入，自己决定加载哪些插件、调用哪些工具、按什么顺序组合；能力可插拔、可勾选，未来持续加入更多法律基础工具。

### 1.1 和"LLM + 数据库 MCP"的区别

单个数据源插件本身与该数据源自己的 MCP 服务没有区别，也不需要有区别。`a2aj` 插件甚至可以直接用 A2AJ 的 MCP 实现（§7）。单纯"搜判例"这件事，我们不比 A2AJ 强，不假装强。

我们的价值只在三处：

1. **模型不经手事实。** 数据源返回的是带来源的记录，模型只拿到编号；引文由格式化功能从记录生成；"已核验"由字段来源推导，而不由模型判断。
2. **跨数据源的来源追溯。** 判例、法规、议案、书、文章、用户补充的字段、文件里提取的字段，都在同一个契约下流转，可信度一路传递到最终成品，比如一整份参考文献。
3. **组合。** 同一次对话里，检索、选择、格式化、核对原文、汇总成参考文献可以连成一条链，每一步都能追溯来源。

去掉第 1 点，它就只是聊天加搜索。

### 1.2 渊源

这套设计是几个成熟领域的组合：

- **数据来源追溯（data provenance）**：where-provenance、W3C PROV，解决"一个值能不能追溯回它的源头"。
- **污点追踪（taint tracking）**：给数据打"可信 / 不可信"标签，标签随数据流转自动传播，到达敏感操作前必须清洗。我们给字段标来源、沿推导链检查，是同一个模式。
- **法律引文治理系统**：KeyCite、Shepard's 把判例的后续处理压缩成一眼看懂的信号，背后必须有真实数据支撑。
- **RAG grounding**：多数检索增强应用把来源当"软提示"。我们把它做成硬闸门：来源不合格，就不给"已核验"。

### 1.3 非目标

- **不做合同 / 文书审查类技能。** 这是 prompt 层的判断，无法核验。
- **不做法律问答、法律意见。** 对话只服务于找来源、生成格式、核对原文这类可验证的工作。模型可以说明自己做不了什么，但不回答"这个原则怎么适用"。
- **不建在 Cordis 上，不引入外部 agent 框架。** 我们的工具绝大多数是无状态函数调用，agent 循环本身很薄（§5），自己实现比依附一个未稳定的运行时更可控。
- **暂不做 `case.treatment`（判例后续处理）。** citator 数据基本被 Westlaw / Lexis 掌握。将来做的话，必须用 `Finding.coverage = "partial"` 诚实表达覆盖不全。

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ 界面：对话框 + 设置栏（模型、API Key、插件勾选与设置）          │
│       插件可以注册自己的结果渲染和面板                         │
├──────────────────────────────────────────────────────────────┤
│ Harness（领域无关，不可关闭）                                  │
│   · Agent 循环：渐进加载插件，原生工具调用                      │
│   · 记录存储：会话内保存记录 / 成品 / 结论，模型只拿编号          │
│   · 契约检查：插件类别 → 可产出的来源类型；返回值校验            │
│   · 回复检查：模型文字里的事实必须能在会话里找到出处             │
│   · 用户补充字段：唯一能写入"用户"来源的入口                    │
├──────────────────────────────────────────────────────────────┤
│ 插件（可勾选）                                                │
│   数据源：a2aj · legisinfo · crossref · openlibrary            │
│   提取：  file · web                                          │
│   功能：  mcgill · quote · bibliography · deadlines            │
├──────────────────────────────────────────────────────────────┤
│ 连接器与规则库（插件内部使用）                                  │
│   local_tools/*_api.py · mcgill_rules.json                    │
├──────────────────────────────────────────────────────────────┤
│ 外部服务：OpenRouter · A2AJ · LEGISinfo · Crossref · …         │
└──────────────────────────────────────────────────────────────┘
```

插件之间互相不认识，只认契约。harness 不懂法律，只负责按契约保存和转交数据。

## 3. 契约层

契约层是整个平台唯一不能松动的部分，由 harness 持有。数据结构的实现在 `core/tool_contracts.py`。

### 3.1 数据结构

```python
Field(value, origin, source_id=None, rule_id=None, span=None, derivation=None)
    # origin ∈ {"database", "extracted", "user", "computed"}，闭集

Record(record_type, fields: dict[str, Field], provider, record_id)
    # record_type：case / legislation / regulation / bill / book / article / webpage / ...

Artifact(kind, content, derivation)        # 引文、参考文献、期限日期……
Finding(verdict, detail, coverage, derivation)
    # verdict ∈ {confirmed, contradicted, inconclusive}；coverage ∈ {complete, partial}

Derivation(inputs: tuple[Field | Derivation, ...], rule_id, input_names)
```

- `database` 字段必须带 `source_id`（数据源 + 记录标识，比如 `a2aj:2022 SCC 39`、`legisinfo:45-1/C-63`）。
- `extracted` 字段应带 `span`（在原文中的位置），便于用户核对。
- `computed` 字段必须带推导链，推导链的叶子必须是其他三种来源的字段。
- `user` 字段的值必须能在用户本次会话的原话里找到。

### 3.2 谁能产出什么

| 插件类别 | 输入 | 输出 | 能产出的字段来源 |
|---|---|---|---|
| 数据源 | 查询条件 | Record | 只能是 `database` |
| 提取 | 附件、网址 | Record | 只能是 `extracted` |
| 功能 | 记录或成品的编号，用户原话 | Artifact / Finding | 只能是 `computed`，或者原样引用输入字段 |
| harness 内置"用户补充" | 用户原话 | Field | 只能是 `user` |

插件注册时检查声明，工具返回时再逐字段检查一次，超出类别权限的一律拒绝。这是早期拦截，不代表插件可信：插件代码按已安装的可信代码对待，没有沙箱。

### 3.3 可信度推导

- **已核验**：成品推导链上的每一个叶子字段都是 `database` 且带 `source_id`。只要有一个 `user` 或 `extracted` 字段，就是未核验。
- 可信度沿推导链逐层传递。一份参考文献由多条引文组成，其中任何一条未核验，就标出这一条。
- 功能插件在"去掉某些字段"的场景下可以重新推导。例如参考文献不含定位引用，所以脚注里由用户填写的定位引用不影响参考文献条目的核验状态。
- `grounded`（可追溯）不等于"正确"，也不等于"适用"。正确性由具体功能的结论（Finding）表达。

### 3.4 引用而不是复制

- 工具返回的 Record / Artifact / Finding 由 harness 存入会话，分配编号（`rec_1`、`art_2`、`fnd_3`）。
- 模型收到的是编号加摘要，摘要仅供它判断和向用户说明。
- 功能工具的参数只接受编号，从存储里取原始对象。模型无法把一个值改写后再传进去。
- 需要用户信息的参数（引语、补充字段）由工具声明"必须出自用户原话"，由 harness 统一检查。检索词不受此限：检索词只是查询条件，结果由数据源决定，可以由模型提出。

## 4. 插件

### 4.1 清单

```python
Plugin(
    name, title, category,       # category ∈ {"source", "extract", "function"}
    description,                 # 一句话，加载前模型只看到这一句
    instructions,                # 加载后加入系统提示
    tools,                       # 名称、描述、pydantic 参数、处理函数
    actions,                     # 界面按钮直接触发的确定性操作，不经过模型
    record_types,                # 数据源 / 提取：产出哪些记录类型；功能：接受哪些
    fact_patterns,               # 本领域的"事实形状"，供 harness 检查模型回复（§5.4）
    ui, settings, default_enabled,
)
```

内置插件放在 `plugins/` 下；第三方插件通过 entry point `citecounsel.plugins` 在启动时加载，不做运行时热加载。

### 4.2 插件目录

| 插件 | 类别 | 工具 | 底层 |
|---|---|---|---|
| `a2aj` | 数据源 | 按名称 / 引用号查判例、查法规；全文检索（法院层级、日期、按最新排序）；取判决全文 | `local_tools/a2aj_api.py` 或 A2AJ MCP |
| `legisinfo` | 数据源 | 按编号查议案；按关键词查议案（按最新排序） | `local_tools/legisinfo_api.py` |
| `crossref` | 数据源 | DOI 查询；按标题 / 作者查文章 | `local_tools/crossref_api.py`、`core/bibliographic.py` |
| `openlibrary` | 数据源 | ISBN 查询；按书名 / 作者查书 | `local_tools/openlibrary_api.py`、`core/bibliographic.py` |
| `file` | 提取 | 从上传的 PDF / DOCX / 图片提取字段 | `local_tools/file_extractor.py` |
| `web` | 提取 | 网络搜索（搜索服务由用户在设置中选择）；读取网页 | `plugins/web` |
| `mcgill` | 功能 | 记录 → 引文；列出某类记录缺哪些字段 | `core/mcgill_format.py`、`mcgill_rules.json` |
| `quote` | 功能 | 引语 + 带全文的记录 → 结论（逐字核对）+ 段落号 | `core/quote_check.py` |
| `bibliography` | 功能 | 若干引文 → 参考文献（分节、排序、去定位引用） | `core/bibliography.py` |
| `deadlines` | 功能 | 起算日、天数、休息日、节假日 → 日期（输入全部回显） | `plugins/deadlines`、`core/legal_tools.py` |

harness 内置两个始终可用的工具：
- `record.add_user_field(ref, field, text)`：用户补充或修改字段，产出 `user` 来源、形成新版本；
- `record.new(record_type)`：数据库里找不到时，建立一条空记录，由用户逐项填写。

### 4.3 设计要求

- **一个插件只做一件事。** 数据源插件不做格式化；格式化插件不去查数据库。
- **数据源插件诚实表达覆盖范围。** 结果为空时说明查了哪里、没查哪里，而不是说"不存在"。
- **功能插件校验输入类型。** 例如 `mcgill` 收到它不支持的记录类型时明确拒绝，并说明需要什么。模型会据此换一个数据源，或者让用户补充。
- **插件可以自带界面**，负责渲染自己的结果、注册自己的面板，也可以给其他插件的结果卡片加按钮（例如"加入参考文献"）。没有界面的插件用 harness 的通用卡片。

## 5. Agent 循环

### 5.1 渐进加载

1. 系统提示里列出：
   - 已启用插件的一句话描述；
   - 已安装但未启用的插件名称和描述，并注明"已关闭，用户可在设置中打开"。
2. 模型通过 `load_plugin` 加载需要的插件；加载后，下一步才看到这个插件的工具和使用说明。加载状态在本次会话内保持。
3. 模型调用工具。harness 校验参数、执行、检查契约、保存记录，把摘要返回给模型，把展示块交给界面。
4. 一步里所有结果都标为"已完成"时，模型再回应一次（不再提供工具），然后结束本轮。
5. 限制：最多 6 步；工具连续两步全部失败就停止。

启用只表示"可以被选用"，不会运行任何东西。

### 5.2 输入提示

DOI、ISBN、中立引用号、议案编号这类格式固定的输入，harness 可以把"它看起来像什么"作为提示附在用户消息后面，例如"含 DOI：10.xxxx/…"。最终用哪个插件，仍然由模型决定。这里只给提示，不做路由。

### 5.3 模型

- 设置栏选择一个对话模型，用于理解输入、选择工具和组织语言。模型能力直接决定选工具的质量。
- 规格不指定模型，但验收（§9）必须在实际使用的模型上通过。
- 按角色分模型（提取、分类、长文档）留到有插件真正需要时再做。

### 5.4 回复检查（harness 层，始终生效）

- 每个启用的插件可以声明 `fact_patterns`，也就是本领域的事实形状：判例引用号、议案编号、年份、段落号、DOI 等。
- 模型的每一段文字，不论本轮是否加载或调用了插件，都用所有启用插件的事实形状检查一遍：出现的事实必须能在本会话的用户原话或已存储的记录、成品、结论里找到。
- 检查不通过的整段隐藏，并明确告诉用户"已隐藏"。
- 模型不得在文字里写出完整引文；引文以卡片为准。

## 6. 界面

- **对话框**：用户消息、模型回复、插件的结果卡片，以及一行"调用了哪个插件的哪个工具"的标记。
- **设置栏**：
  - 模型 ID 和 API Key（只显示是否已设置，不回显）；
  - 插件按类别分组显示：数据源、提取、功能。每个插件带开关、一句话说明、工具列表和它自己的设置项。
- **右侧面板**：只在有插件注册面板时出现，例如参考文献清单。
- **插件界面接口**：`registerBlock`、`decorate`、`registerPanel`、`action`。插件界面代码按"已安装即可信"对待。

## 7. MCP

两个方向，都以契约为边界：

- **把外部 MCP 服务当插件用。** 适配层把外部工具的输出转成 Record。只有当适配层能给出稳定的记录标识，并且确认数据来自该数据源时，字段才能标为 `database`；否则一律标为 `extracted`。A2AJ 的 MCP 是第一个候选。
- **把我们的插件暴露为 MCP 服务**，供其他 harness 调用。输出里带上字段来源和推导链。等契约和首批插件稳定后再做。

## 8. 实施

### 8.1 现有代码

| 部分 | 位置 | 状态 |
|---|---|---|
| 界面、agent 循环、渐进加载、会话、设置 | `harness/` | 可用 |
| 回复检查（harness 层，段落级隐藏） | `harness/grounding.py` | 已按 §5.4 上收 harness；插件以 `fact_patterns` 声明事实形状 |
| 契约数据结构 | `core/tool_contracts.py` | 已并入 harness 的记录存储（`harness/records.py`：编号、类别检查、`record.new` / `record.add_user_field`） |
| McGill 渲染与规则 | `core/mcgill_format.py`、`mcgill_rules.json` | 已包装为 `mcgill` 插件 |
| 引语定位 | `core/quote_check.py` | 已包装为 `quote` 插件；`a2aj.full_text` 取全文 |
| 参考文献 | `core/bibliography.py` | 已包装为 `bibliography` 插件；签名机制移除（存储侧编号取代客户端签名） |
| 书目检索 | `core/bibliographic.py` | 已并入 `crossref` / `openlibrary` 插件 |
| 数据源到 Record 的适配 | `core/source_tools.py` | 已包装为 `a2aj` / `legisinfo`（含关键词检索）/ `crossref` / `openlibrary` 插件 |
| 期限计算 | `core/legal_tools.py`、`plugins/deadlines` | 已是插件，产物入库为 Artifact |
| 网络搜索与网页读取 | `plugins/web` | 已是插件；fetch 产出 `extracted` 记录 |
| 文件提取 | `plugins/file`、`local_tools/file_extractor.py` | 已是插件（无模型分类调用） |
| 数据源连接器 | `local_tools/*_api.py` | 可用 |
| MCP（§7） | — | 未做；等契约与首批插件稳定 |

### 8.2 步骤

每一步完成后都能运行，测试保持全部通过。1–5、6 已完成（见 `docs/HARNESS.md`）：

1. ~~**harness 记录存储**~~ ✅：会话内保存 Record / Artifact / Finding 并分配编号；按插件类别检查来源权限并校验返回值；内置 `record.add_user_field` 与 `record.new`。
2. ~~**数据源插件**~~ ✅ `a2aj`、`legisinfo`（含按关键词检索）、`crossref`、`openlibrary`：基于 `core/source_tools.py` 与连接器，输出 Record。
3. ~~**功能插件** `mcgill`~~ ✅：输入记录编号，输出引文 Artifact，"已核验"由推导链计算。
4. ~~**功能插件** `quote`、`bibliography`~~ ✅：只接受记录 / 成品编号。
5. ~~**提取插件** `file`~~ ✅；`web` 已改为输出 `extracted` 记录。
6. ~~**回复检查移入 harness**（§5.4）~~ ✅；系统提示列出已关闭的插件（§5.1）✅。
7. **MCP**（§7）：在契约和首批插件稳定后进行。

## 9. 验收场景

以下场景在设置栏实际选用的模型上通过：

| 输入 | 期望 |
|---|---|
| `[1999] 1 SCR 688` | `a2aj` → `mcgill`：`*R v Gladue*, [1999] 1 SCR 688.` 已核验 |
| `gladue` → `我要的是最有名的那个` | 候选中含 SCC 1999；第二句选中它，已核验 |
| `给我一个最新的、提到 Gladue 原则的省级或 SCC 判例` → `引用第一个` | `a2aj` 全文检索，按最新排序、限定法院，写明检索了哪些法院；引用后已核验 |
| `加拿大最近限制未成年人使用社交媒体的 bill` | 加载 `legisinfo` 按关键词检索；模型文字里不出现记录之外的议案编号 |
| `我要引用 Miranda rights 那个美国本身的 case` | 说明没有启用的美国判例数据源；如果 `web` 已安装但关闭，提示可以在设置中打开；不编造引用 |
| `Hart The Concept of Law` | `openlibrary` 候选 → `mcgill` 书籍引文；缺出版地时，通过 `record.add_user_field` 补充，结果未核验 |
| `Edwards v Canada AG 1929` → `是判例，Privy Council，[1930] AC 124` | 数据源都查不到 → `record.new` → 用户补充字段 → `*Edwards v Canada (AG)*, [1930] AC 124 (PC).` 未核验 |
| `2022 SCC 39` → 核对一句引语 → 加上段落号 | `a2aj` 取全文 → `quote` 定位 `at para 1` → 定位引用取自数据库原文，仍然已核验 |
| 生成参考文献 | 分节、排序、去定位引用；每一条的核验状态由推导链计算 |

契约测试：
- 越权产出来源类型的插件被拒绝；
- 模型传入不存在或其他会话的编号被拒绝；
- "必须出自用户原话"的参数传入模型编造的值被拒绝；
- 模型回复中的未出处事实被隐藏，不论本轮是否调用了插件。

## 10. 已定决策

| 问题 | 决定 |
|---|---|
| 插件粒度 | 一个数据源或一项功能一个插件 |
| 路由 | 由模型决定；代码只给输入提示（§5.2） |
| 插件间数据传递 | 通过 harness 的记录存储和编号，插件之间不直接依赖 |
| 工具可见性 | 渐进加载；已关闭的插件也列出，并注明已关闭 |
| 回复检查 | harness 层，对所有回合生效，事实形状由插件声明 |
| 界面 | 对话框 + 设置栏；插件自带界面 |
| 热加载 | 不做；启动时发现插件 |

## 11. 待定

1. **A2AJ 数据源用自己的连接器，还是接 A2AJ 的 MCP。** 前者可控，后者少维护；取决于 A2AJ MCP 能否给出稳定的记录标识。
2. ~~**会话持久化。**~~ **已定（2026-09-23）**：本地持久化。每会话一个 JSON 文件（消息、插件状态、全部编号对象、附件索引），附件落会话目录；重启后 `SessionStore.get` 凭 token 哈希复活会话，编号续排，过期清扫。工程细节见 `docs/HARNESS.md` §7。
3. **引文版本的界面表达。** 用户补充字段会形成新版本，界面上是否需要显示版本历史和撤销。
