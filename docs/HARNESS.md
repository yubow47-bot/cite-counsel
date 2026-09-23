# Cite Counsel Harness：工程架构

状态：与代码同步（2026-09-23）。设计总纲见 `docs/LEGAL_TOOL_PLATFORM_BLUEPRINT.md`（下称"蓝图"）；
本文写的是 harness 层的工程事实——模块、数据流、契约检查点、以及刻意留下的边界。

## 1. 模块

| 模块 | 职责 |
|---|---|
| `harness/core.py` | Harness 对象：设置、agent 循环、工具执行（含 UserText 参数核验）、grounding 检查、内置 `record__*` 工具 |
| `harness/plugin.py` | 插件接口与发现：`Plugin` / `Tool` / `Result` / `Setting` / `UserText`；启动时扫描 `plugins/` 与 entry point，不热加载 |
| `harness/records.py` | 会话记录存储：编号分配（`rec_N` / `art_N` / `fnd_N`）、摘要、类别契约检查、**叶子来源对账**、内置记录工具的数据操作 |
| `harness/session.py` | 会话：消息史、已加载插件、每插件状态、附件、记录存储；**本地持久化**（落盘/复活/清扫）；`Context` 是插件在单次调用里看到的一切 |
| `harness/grounding.py` | 回复检查（§5.4）：事实形状匹配、段落级隐藏 |
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
  - `user` 来源只能由 harness 内置工具写入（见 §5）。
- 检查时机：`ctx.save()` 时逐对象检查（`Store.check_output`），越权即抛 `ContractError`，
  工具结果以 `{"error": "contract violation"}` 返回给模型。
- **叶子来源对账**：保存 Artifact / Finding 时，推导链逐叶子审计（`Store._audit`）。
  存储维护本会话的证据库——每条入库字段与推导叶子的 `(origin, value, source_id)`：
  - `database` 叶子必须对上证据库里的同 `(value, source_id)` 条目——function 插件无法
    伪造一个"标着 database"的叶子让成品被算成已核验；
  - `computed` 叶子必须带推导链；
  - `user` 叶子必须来自本调用经 harness 核验的切片（`UserText` 参数）、本调用的参数值，
    或已在证据库中的字段；
  - `extracted` 叶子必须对上已入库的提取记录。
- **UserText 参数**：工具把"必须出自用户原话"的参数声明为 `harness.plugin.UserText`，
  harness 在执行前对每个该类参数跑 `user_said`（§3.4），把**核验后的切片**替换进参数——
  插件永远拿不到模型的转述版。
- "已核验"不由任何人声明：由推导链计算（`core.tool_contracts.grounding_issues`）。
  推导链叶子全为 `database` 且带 `source_id` 才算核验；一个 `user`/`extracted` 叶子就是未核验。

### 刻意留下的边界（不是疏漏）

1. **插件不是沙箱。** 契约检查拦截的是类别误用与伪造出处（早期拦截），插件代码本身按
   已安装的可信代码对待。`database` 叶子的对账堵死了"已核验"造假的路；`user` 叶子的两级
   宽严（UserText 严格核验 / 普通参数值只作标签）不影响核验结论——user 叶子本来就不产生
   已核验。规格 §3.2 明示插件可信边界。
2. **user 来源值的校验是字符串级的。** `user_said` 要求值是用户原话的子串（空白归一、
   忽略大小写）。模型转述（"下周五"→"2026-09-25"）会失败，这是设计：fail closed。
3. **编号属于会话。** 存储随会话文件生灭；别的会话的编号查不到，即拒绝。
4. **无沙箱、无签名。** 旧版把引文快照签名后交给客户端保管；现在存储是服务端会话状态、
   编号是唯一句柄，签名机制随之移除（`core/bibliography.py`）。

## 4. Agent 循环（§5）

1. 系统提示列出：已启用未加载插件（供 `load_plugin`）、**已安装但关闭的插件**（注明用户可在设置打开，
   模型不得假装有这个数据源）、已加载插件的说明。
2. 固定格式输入（DOI / ISBN / 中立引用号 / 议案编号）由 harness 识别后**作为提示**附在消息后；
   用哪个插件仍由模型决定，代码不做路由。
3. 工具连续两步全部失败即停止；一步内所有结果都 `final` 时，模型不再带工具地回应一次。
4. 内置工具始终可用：`record__new`（空记录，用户逐项填写）、`record__add_user_field`
   （值必须出自用户原话，形成记录新版本）。这是"用户补充字段"的唯一入口。

## 5. 回复检查（§5.4）

- 每段模型文字（无论本轮是否加载/调用了插件）都用**所有启用插件**的 `fact_patterns`
  加 harness 内置形状（年份、判例引用、DOI、定位引用）检查。
- 事实必须能在本会话找到：用户原话或工具返回内容（记录字段值都在工具结果里）。
- **段落级隐藏**：只隐藏带未出处事实的段落，干净的段落保留，并提示"已隐藏"。
- 模型自己的旧 prose 不算依据——否则编造的事实会被"说过一遍"洗白成"已出处"。

## 6. 插件清单与类别

| 插件 | 类别 | 工具 | 底层 |
|---|---|---|---|
| `a2aj` | source | find_case / find_legislation / full_text | `core/source_tools.py`、`core/quote_check.judgment` |
| `legisinfo` | source | bill / bills（关键词检索，标题全词匹配、最新在前） | `core/source_tools.py`、LEGISinfo 连接器 |
| `crossref` | source | doi / article | `core/source_tools.py`、`core/bibliographic.py` |
| `openlibrary` | source | isbn / book | 同上 |
| `file` | extract | extract | `local_tools/file_extractor.py`（无模型分类调用） |
| `web` | extract | search / fetch | DuckDuckGo Lite、`extract_from_url` |
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
- **契约测试**（规格 §9）：越权产出来源、跨会话/不存在编号、"必须出自用户原话"、未出处事实
  隐藏、伪造 database 叶子、持久化往返——见 `tests/test_harness.py`、`tests/test_function_plugins.py`、
  `tests/test_extract_plugins.py`、`tests/test_persistence.py`。