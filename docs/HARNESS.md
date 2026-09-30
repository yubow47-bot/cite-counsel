# Cite Counsel Harness：当前架构

状态：2026-09-30。本文记录运行代码的工程细节；产品规格见 `LEGAL_TOOL_PLATFORM_BLUEPRINT.md`，两者均以代码为准。

## 原则与边界

主模型决定何时回答、读取 Skill、加载插件、调用工具、继续或停止。Harness 执行权限、参数、会话隔离、预算与来源身份检查；插件执行检索、提取、计算和格式化。工具调用成功不等于任务完成，记录可追溯不等于法律结论正确。

普通能力通过新增插件工具或 Markdown Skill 扩展。涉及新权限或证据类型时，仍须扩展相应契约。`record__compose` 的 McGill 专属实现已移到 `plugins/mcgill/compose.py`；它仍由核心注册为兼容内置工具，这是当前剩余的注册耦合。

## Skill 与工具

`harness/skills.py` 扫描 `skills/*.md`、内置插件目录下的 `SKILL.md`，以及插件显式声明的 `skill` 路径。Skill 首行是标题，第二行以 `> ` 开头的一句话是目录说明。系统提示只列可用 Skill 的名字和说明；`read_skill(name)` 才返回正文。插件所属 Skill 只在插件启用时可读，读取它不加载该插件的工具；`load_plugin` 只暴露工具 Schema，不注入领域说明。工具与 Skill 没有一一对应要求。

## 单一执行循环

`run_turn_stream` 使用 `_turn_events`。非流式 `run_turn` 收集同一生成器的事件，不再维护第二份工具循环。每一步动态构建一般执行原则、可用插件/Skill 目录和工具 Schema。最多 20 轮工具步骤；仅加载插件的轮次不计，但总迭代仍有界。`Result.final` 是产物状态，不切断模型后续工具。预算耗尽时给模型一次无工具的收尾调用。

`harness/llm.py` 处理协议、流式片段、`<think>` 与文本形式的工具调用。`ModelProfile` 按模型前缀控制这些兼容处理；未知模型使用通用兼容配置。核心循环不检查模型名称。

## 记录与材料

`SessionStore` 持久化会话 JSON，并把每步实际的模型请求、模型返回、工具调用和工具结果追加到 `<session_id>.events.jsonl`。模型请求事件含当步系统提示、历史消息、工具 Schema、顺序和模型名称；`read_skill` 的正文以工具结果进入事件记录。事件日志支持追查当时给了模型什么，不保证重新调用模型会得到相同输出，也不提供外部防篡改证明。

`record__read(ref, field, offset, limit)` 让模型按需读取本会话记录里的文本字段，单次最多 4,000 字符，返回片段、总长度、下一个偏移和字段来源。网页 `fetch` 成功时保存响应字节、请求与最终 URL、取回时间、SHA-256、HTTP 状态及部分响应头，并把快照信息关联到记录。用户可从网页卡片或带快照的来源标注打开本会话保存的原始响应；接口以会话令牌授权，并以纯文本展示 HTML 字节，避免把源网页脚本当作本站代码运行。`version_as_of` 当前为空；取回时间不能代替法条版本日期。其他来源插件的原始响应快照尚未接通。

会话 JSON 和事件/来源快照随会话过期或删除清理。附件和网页来源快照存于会话自己的目录。

## 契约与错误

`Context.save` 维持记录类别和推导叶子的来源审计。`UserText` 与普通参数只有在核对到用户原话后才能支持 `user` 来源；普通模型参数不会因为调用过工具就升级成用户输入。`record__compose` 不再要求先搜网；缺乏来源的字段仍写作 `model`，字段形状错误仍报告并不写入。

Harness 产生的错误带 `kind` 与 `retryable`；相同调用只在确定原样重试无用时去重。插件返回的普通错误保留其内容，模型可见路径补充 `tool_reported_error` 与未知可重试性。空结果不自动等同于异常。未知工具异常报告 `unknown_error`，不猜测成暂时性网络故障。

`harness/grounding.py` 保留回复中的事实形状与文本出处标注。索引只读取具有非 `model` 来源的记录字段和用户原话，不把工具 JSON 或模型写入的字段当作证据。这仍只是文本匹配提示，不能视作事实真伪、来源权威性或法律适用的核验。

文本比对统一保留词与数字边界，不删除全部空白。数据库全文中的片段只能成为 `extracted`，不能通过子串继承数据库核验状态。Compose 只有完整复制同类型记录的对应字段时才保留 `database`；混用不同来源身份的字段会降为 `extracted`。直接传入的 pinpoint 只按用户原话或模型输入标记；quote 生成的 pinpoint 绑定作品身份，跨作品调用会报错。

`mcgill__cite` 接受 `ref` 或固定法律名称 `title`，如 `Charter`。固定格式来自规则表，返回 `format_checked: true`，不会据此宣称法条文本或 pinpoint 已核验。`constitutional` 也有独立记录 Schema，支持从已取得材料组成的宪法引文。

## Harvey LAB 的参考范围

[Harvey LAB 的公开架构](https://github.com/harveyai/harvey-labs/blob/main/docs/architecture.md)展示了封闭材料集、少量通用文件工具、沙箱和可审阅产物的组合。这里借鉴“材料必须能被 Agent 读取、循环保持通用”的原则；本产品还有在线来源与引文记录，因而保留专业检索和格式工具。LAB 的六个工作区工具及 Podman 配置不直接复制到本应用。
