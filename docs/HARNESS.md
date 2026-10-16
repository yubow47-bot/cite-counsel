# Cite Counsel Harness：当前架构

状态：2026-10-15。本文记录运行代码的工程细节；产品规格见 `LEGAL_TOOL_PLATFORM_BLUEPRINT.md`，问题主线见 `INQUIRY_WORKFLOW.md`，均以代码为准。

## 原则与边界

主模型决定何时回答、读取 Skill、加载插件、调用工具、继续或停止。Harness 执行权限、参数、会话隔离、预算与来源身份检查；插件执行检索、提取、计算和格式化。工具调用成功不等于任务完成，记录可追溯不等于法律结论正确。

普通能力通过新增插件工具或 Markdown Skill 扩展。涉及新权限或证据类型时，仍须扩展相应契约。`record__compose` 的 McGill 专属实现已移到 `plugins/mcgill/compose.py`；它仍由核心注册为兼容内置工具，这是当前剩余的注册耦合。

默认 `inquiry` 插件将问题、关键未知、求证任务、实际工具执行与论证检查接到同一个精确版本存储。现有来源/文件/网页工具负责取得材料，research 负责摘录、依据类型和推论，inquiry 负责面向问题的继续调查与答复。核心只提供通用执行回执和状态/检查hooks，不预设固定法律工作流。

## Skill 与工具

`harness/skills.py` 扫描 `skills/*.md`、内置插件目录下的 `SKILL.md`，以及插件显式声明的 `skill` 路径。Skill 首行是标题，第二行以 `> ` 开头的一句话是目录说明。系统提示只列可用 Skill 的名字和说明；`read_skill(name)` 才返回正文。插件所属 Skill 只在插件启用时可读，读取它不加载该插件的工具；`load_plugin` 只暴露工具 Schema，不注入领域说明。工具与 Skill 没有一一对应要求。

## 单一执行循环

`run_turn_stream` 使用 `_turn_events`。非流式 `run_turn` 收集同一生成器的事件，不再维护第二份工具循环。每一步动态构建一般执行原则、可用插件/Skill 目录和工具 Schema。最多 20 轮工具步骤；仅加载插件的轮次不计，但总迭代仍有界。`Result.final` 是产物状态，不切断模型后续工具。预算耗尽时给模型一次无工具的收尾调用。

`harness/llm.py` 处理协议、流式片段、`<think>` 与文本形式的工具调用。`ModelProfile` 按模型前缀控制这些兼容处理；未知模型使用通用兼容配置。核心循环不检查模型名称。

每批最多 12 个工具调用、每轮用户请求最多 60 个调用；循环在调用边界和流式事件之间检查 300 秒执行预算。正在阻塞的网络请求受其自身超时约束，停止不是强杀线程。模型流的 error、不完整结束和异常 finish reason 不会提交为最终回答或执行半截工具。断开连接或点击 Stop 发出取消信号；中断时补齐尚未返回的工具回执，使下一轮请求保持合法。删除设墓碑，旧 worker 不得重建会话、事件日志或来源快照。

## 记录与材料

`SessionStore` 把会话快照存在 `sessions.db`（SQLite）：每个会话一行，每条消息、界面卡片、执行回执、记录和小型状态各一行。每次保存只写入内容变化了的行，并在一个事务里完成；没变的记录不会重新编码。保存失败会回滚，下次保存补写所有差异。旧版本留下的 `<id>.json` 在启动时导入一次，原文件保留作备份；已导入或被显式删除的会话不会再被导入。每步实际的模型请求、模型返回、工具调用和工具结果仍追加到 `<session_id>.events.jsonl`。模型请求事件含当步系统提示、历史消息、工具 Schema、顺序和模型名称；`read_skill` 的正文以工具结果进入事件记录。事件日志支持追查当时给了模型什么，不保证重新调用模型会得到相同输出，也不提供外部防篡改证明。

`record__read(ref, field, offset, limit)` 让模型按需读取本会话记录里的文本字段，单次最多 4,000 字符，返回片段、总长度、下一个偏移和字段来源。网页 `fetch` 成功时保存响应字节、请求与最终 URL、取回时间、SHA-256、HTTP 状态及部分响应头，并把快照信息关联到记录。用户可从网页卡片或带快照的来源标注打开本会话保存的原始响应；接口以会话令牌授权，并以纯文本展示 HTML 字节，避免把源网页脚本当作本站代码运行。`version_as_of` 当前为空；取回时间不能代替法条版本日期。其他来源插件的原始响应快照尚未接通。

`record__list` 提供有界材料及产物目录，系统提示列出最近的材料编号；`record__search` 在保存的原文中查找字面词组，返回准确字符位置和已知页段位置。`record__read` 也能读取 Artifact 内容与 Finding 详情，`exact=true` 固定旧版本。Store 的 `get_exact`、`current_ref`、`fingerprint` 区分不可变版本和最新别名；supersedes 关系随快照保存与恢复。

文件插件保存完整提取文本、来源文件 SHA-256、`extraction_segments` 和 `extraction_coverage`。DOCX 为正文与表格，PPTX 为幻灯片文字和表格，XLSX 为各表行/公式；未覆盖的文件部分列入 excluded_parts。PDF 保存所有可读文字页；扫描页每次至多 OCR 三页，报告缺失页，可用 start_page/end_page 继续并合并同一附件版本。提取覆盖不证明 OCR 准确。工具仅返回短 preview，截断预览不会丢弃底层材料。

普通会话快照、事件日志和来源快照随会话过期或删除清理（删除也会移除对应的旧 JSON 备份）。创建调查的会话标记 `retained=true`，跳过过期及磁盘数量清理，直到显式删除。附件和网页来源快照存于会话自己的目录；当前调查仍绑定所属对话，尚无跨对话项目库。

未保留的会话默认闲置 30 天过期，恢复时检查真实过期时间。前端本地保存会话令牌和名称，不再按30项静默裁掉旧调查入口；凭令牌恢复文字、卡片和材料。刷新、开关插件和 New chat 不删除旧研究。Delete current conversation 才调用删除。并发恢复复用同一 Session 和执行锁。令牌只存在本机浏览器，清除浏览器存储会失去界面恢复入口；本地文件不加密。

## 实际执行与当前任务状态

`harness/receipts.py` 在工具执行完成后签发运行ID，保存类别、ok/no_match/error、覆盖信息、结构化输出引用及指纹。读取来源时额外保留实际字段、返回区间和文本哈希，搜索匹配区间与返回上下文区间分别记录。空读取、模型字段读取、生成草稿读取不能充当来源调查；来源原始字段中的ref字样不进入系统输出绑定。回执证明执行和取得的材料，相关性与充分性仍待判断。

每条事件另带 harness 自己的 `state`：轮次编号，以及运行中的阶段、剩余秒数和已用调用数；每轮以 `turn_start` 开始。界面的 Run log 按轮列出模型请求、工具调用、复核和交付，显示可从请求／返回配对计算的耗时及回执状态；旧日志缺的字段显示为未记录。展开 Raw event 按需读取该事件保存的完整内容，使用当前会话凭据，不向对话历史写入信息。

插件只通过 `Context` 的公开方法接触 harness：`use(name)` 取得已在 `requires` 声明的插件，`model`、`is_enabled(name)`、`log_event(kind, data)`、`save_source(...)`、`request_review(...)`。`plugins/` 下出现 `._harness` 会使测试失败。

模型可以按阶段配置为 `models: {decide, review, completion}`，留空的阶段使用默认 `model`。`decide` 处理普通工具决策与普通答复；`review` 处理一次候选答复复核，包括负责复核和保存的 `final_completion`；`completion` 处理预算耗尽后的无工具答复生成。设置弹窗提供对应输入框，事件日志记录实际使用的模型。旧 `review_model` 设置和接口继续映射到 `models.review`。少数模型拒绝指定工具的 `tool_choice`，此时同一请求改用 `auto` 重试一次，harness 仍只接受恰好调用指定工具的回复。所有阶段共享本轮截止时间和原有复核额度。

Plugin 的可选 `context_provider(ctx)->str` 将保存的当前任务数据重新带入每轮输入；`final_response_checks(ctx,text)->list[dict]` 提供最终答复旁的检查卡片。错误会如实提示。只读动作不签发调查执行回执。问题工具通过受信回执表核对实际执行，无法通过模型参数声称已查证。

## 契约与错误

`Context.save` 维持记录类别和推导叶子的来源审计。`UserText` 与普通参数只有在核对到用户原话后才能支持 `user` 来源；普通模型参数不会因为调用过工具就升级成用户输入。`record__compose` 不再要求先搜网；缺乏来源的字段仍写作 `model`，字段形状错误仍报告并不写入。

Harness 产生的错误带 `kind` 与 `retryable`；相同调用只在确定原样重试无用时去重。插件返回的普通错误保留其内容，模型可见路径补充 `tool_reported_error` 与未知可重试性。空结果不自动等同于异常。未知工具异常报告 `unknown_error`，不猜测成暂时性网络故障。

A2AJ / LEGISinfo 连接器返回带 coverage 的列表，失败抛 SourceRequestError；插件上报 source_unavailable、原因与可重试性。多会期部分失败、缓存年龄和 stale 状态传至结果与记录 metadata，不能伪装成完整的空查询。LEGISinfo 匹配英法标题，当前状态与最后完成活动分别保存；真实在线 feed 的 schema 仍需单独检查。

`harness/grounding.py` 保留回复中的事实形状与文本出处标注。索引只读取具有非 `model` 来源的记录字段和用户原话，不把工具 JSON 或模型写入的字段当作证据。这仍只是文本匹配提示，不能视作事实真伪、来源权威性或法律适用的核验。

文本比对统一保留词与数字边界，不删除全部空白。数据库全文中的片段只能成为 `extracted`，不能通过子串继承数据库核验状态。Compose 只有完整复制同类型记录的对应字段时才保留 `database`；混用不同来源身份的字段会降为 `extracted`。直接传入的 pinpoint 只按用户原话或模型输入标记；quote 生成的 pinpoint 绑定作品身份，跨作品调用会报错。

Compose 修改作品身份时解绑旧正文和定位；新组装记录使用独立身份。Quote 定位绑定准确来源版本、作品身份字段摘要和原文哈希，引用前重新检查。Quote 同时支持文件/网页的 extracted 原文，来源不升级为 database；partial 文本中未匹配只能报告该范围内未找到。引文清洗不改写原始证据叶子，书目根据审计后对象统一计算状态。

## 法律研究对象

Research 功能插件将摘录、前提、论点、结论和草稿保存为版本化 Artifact。原文片段固定到确切记录/字段/字符范围与哈希，支持、限制、反对关系及说话主体作为待审阅断言保存。来源或前提更新沿依赖关系传播为 stale；旧摘录仍可读取，旧人工复核不会继续生效。人工复核仅由界面 action 记录，模型工具不能声称用户已复核。原文对照与 Markdown 下载可从研究卡片打开。接口、示例与 Quick Check 的公开资料对照见 `RESEARCH_WORKSPACE.md`。

`mcgill__cite` 接受 `ref` 或固定法律名称 `title`，如 `Charter`。固定格式来自规则表，返回 `format_checked: true`，不会据此宣称法条文本或 pinpoint 已核验。`constitutional` 也有独立记录 Schema，支持从已取得材料组成的宪法引文。

## Harvey LAB 的参考范围

[Harvey LAB 的公开架构](https://github.com/harveyai/harvey-labs/blob/main/docs/architecture.md)展示了封闭材料集、少量通用文件工具、沙箱和可审阅产物的组合。这里借鉴“材料必须能被 Agent 读取、循环保持通用”的原则；本产品还有在线来源与引文记录，因而保留专业检索和格式工具。LAB 的六个工作区工具及 Podman 配置不直接复制到本应用。

本轮也逐读其 harness/run.py、agent_loop.py 和 evaluation/scoring.py：轻量 eval 采用 task.json + documents、独立 run 目录、轨迹/配置/指标、按交付物范围逐项评分、全部条件通过才算通过。四个合成离线工作流不运行 Harvey 题库或 LLM judge，不是法律质量 benchmark；真实模型能力验收仍未完成。范围与命令见 `eval/README.md`。
