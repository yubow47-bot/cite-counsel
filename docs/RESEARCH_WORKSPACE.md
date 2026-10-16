# Research workspace：保存、检查和更新法律分析

状态：2026-10-14。本文描述当前 `plugins/research/` 与 `core/research.py` 的实际行为。

研究工作区把摘录、前提、论点、结论和草稿保存为会话内的版本化对象。它能检查一段原文绑定了哪个作品、哪个材料版本，以及材料或前提变化后哪些已保存成果需要重审。法律关系仍由模型或研究者提出；工具不把这些关系升级为已经核验的法律结论。

## 五个模型工具

`research` 是默认启用的 `function` 插件。模型先用 `load_plugin(name="research")` 加载工具。产物都是 `Artifact(kind="legal_research")`，JSON 使用 `legal_research.v1` schema；标题和类型也写入材料目录的 `meta.research`。

| 工具 | 主要参数 | 保存或返回的内容 |
|---|---|---|
| `research__excerpt` | `ref, field, start, end, quote, speaker` | 精确原文摘录，绑定作品身份、材料精确版本、字段、字符区间与哈希 |
| `research__claim` | `text, kind, status, evidence, premises, prior_ref, assessment` | 一条前提、论点或结论，以及声明的证据与前提关系；`prior_ref` 创建修订版本 |
| `research__draft` | `title, claim_refs, prior_ref` | 按指定精确版本组成草稿，包含摘录、来源定位、前提与创建时的开放检查项 |
| `research__inspect` | `ref` | 读取指定对象，递归检查证据缺口、版本变化、原文绑定和用户复核状态 |
| `research__list` | `query, limit` | 浏览本会话最新版本的研究对象；查询匹配对象标题、正文或摘录文字 |

`claim.kind` 可为 `claim`、`premise`、`conclusion`。`status` 可为 `unknown`、`supported`、`disputed`，是记录下来的分析判断。证据项形如：

```json
{
  "excerpt_ref": "art_2",
  "relation": "limits",
  "note": "This passage appears to limit the proposed rule to this procedural setting."
}
```

`relation` 支持 `supports`、`limits`、`opposes`，不自动判定该关系是否成立。`premises` 引用其他前提、论点或结论的精确版本。修订会完整替换该对象的文字和链接；调用方必须显式保留仍然适用的关系。修订产物获得新编号，旧内容不被覆盖。`prior_ref` 必须仍是当前版本；过时的修订基线返回冲突。直接或间接依赖被替换的自身版本链也会被拒绝，避免保存一个创建时就失效的循环分析。

## 精确原文与版本

摘录使用半开字符区间 `[start, end)`，要求 `quote == field.value[start:end]`，单段最多 8,000 字符。`start/end` 可以同时省略：工具仅在精确引语于所选原文字段中唯一出现时自动定位；缺失或重复时返回明确错误，需读取或搜索原文后选择位置。显式传入的错误位置不会被自动修正。偏移是 Python 字符串位置，不是 PDF 坐标或浏览器 UTF-16 索引。

`field` 留空时按 `full_text`、`text`、`content` 选择现存字段，并绑定实际字段名。显式给出的字段不存在时返回可用字段列表，不会偷偷改读其他字段。

摘录保存 `provider / record_id / source_type` 作品身份、原始记录编号、记录指纹、字段 SHA-256、引语 SHA-256 和原始来源类别。读取与检查使用 `Store.get_exact`，不会把旧编号静默解析为新全文。`current_ref` 只用于提示已经存在的新版本。

`model` 来源字段不能充当原文。`computed` 字段只有存在完整可审计的推导链时才允许摘录。上传文件仍保持 `extracted` 来源；精确字符匹配不把上传文件变成权威数据库记录。

`speaker` 可标为法院、当事人、不同意见、转引权威、作者或未知，但其状态始终是 `asserted_only`。在材料中找到一句话，并不能机械证明它是法院采纳的裁判理由。

右侧工作区支持搜索与分页，可以浏览对象并打开检查报告。“Open source context” 打开摘录绑定的精确旧材料版本，并从摘录附近开始分页展示。材料已更新时，这个对照入口仍保留原来引用过的内容。默认卡片展示摘要和待查状态，完整快照与版本信息在折叠区中。

## 用户复核与变化传播

模型只能记录 `assessment=pending` 或 `model_assessed`。用户点击 “Record that I read this snapshot” 后，服务端独立的 `research.review` action 才记录 `reviewer=user`、对象指纹和时间。这个 action 不在模型工具目录中。`list/inspect/export` 是只读界面操作，刷新不会向模型历史或聊天记录反复追加卡片。

用户复核表示已查看并确认这个快照；`legal_verification` 仍然是 `false`。复核不会删除证据缺口，也不会把 `unknown` 或 `disputed` 改成已支持。

检查沿保存的依赖关系传播变化：

```text
材料精确版本 → 摘录 → 前提/论点/结论 → 草稿
```

材料有了替代版本、前提或论点被修订、依赖哈希不再相符，或者原始依赖无法取得时，相关检查返回 `stale=true`。此前的用户复核显示为 `review_invalidated`；没有复核过的对象仍显示 `pending`，并带变化问题。旧的摘录和草稿仍可读取。

`mechanical_binding=intact` 与 `stale=true` 可以同时出现：原始摘录仍与旧材料精确匹配，但后来出现了新材料，继续依赖旧结论需要重新审阅。原文绑定不符或依赖缺失显示 `invalid`；检查达到节点上限等情况显示 `incomplete`，不能当成检查通过。

`research__compare(ref)`（也是检查报告里“Compare … with its newer version”按钮）把被引用的旧记录版本和最新版本双栏对照：逐字段高亮删除和新增文字，长段未变文字折叠，两栏可分别打开其精确版本原文；列出旧版本上每条摘录在新版本里的位置（原位、移动、出现多次、已不存在）；按依赖深度列出受影响的论点和草稿。它只读，不改任何绑定。每个受影响节点有“Revise this item”入口，另有整体“Prepare a revision request”；它们只将指定版本和依赖路径的修订请求填进输入框，不发送。修订时从新版本另存摘录，再用 `prior_ref` 逐个保存新版本，旧版本保留。

当前不会自动重写旧成果。继续工作时，应对新材料建立新摘录，修订依赖它的前提或论点，再组成新草稿并重新复核。依赖已经变化的对象不能直接通过复核按钮重新盖章。

## 报告会检查什么

报告返回逐项 `issues`，包括没有 `supports` 摘录的 `support_gap`、未知或有争议的状态、已经链接的反对材料，以及沿依赖路径发现的修订或绑定问题。路径和 `current_ref` 帮助研究者找到需要修改的对象。

这些检查针对已经保存的图：

- `support_gap` 检查有没有声明的支持链接，不判断这段文字是否足以支持论点。
- `opposing_evidence` 提醒已经链接了反对材料，不会主动从全库发现反例。
- `supported` 状态和 `model_assessed` 都是分析声明，不是审阅器的法律判断。
- 递归检查最多访问 400 个研究对象；达到上限会留下检查不完整的问题并要求重审。

草稿 Markdown 是组成草稿时的静态快照。下载会通过后端 `export` action 重新检查，附上导出日期、精确对象与当前版本、阅读确认状态、失效问题，以及递归展开的全部前提和证据。旧草稿仍可导出，但发生变化时会明确标记 STALE；原始 Markdown 不被改写。当前导出包含原文、作品与内部字符定位；它没有自动把所有定位转换成正式 McGill 引注。

证据依赖以精确编号和指纹保存于 JSON。Derivation 中的紧凑引用叶明确保留为模型来源，不借助结构链接声称法律真值已经 grounded；这样也避免每层论点、前提和草稿重复复制整份长文。依赖与材料的实际核对由 `inspect` 完成。

## 与公开 Quick Check 产品的对照

Thomson Reuters 的[官方帮助](https://www.thomsonreuters.com/en-us/help/westlaw-edge/tools/quick-check)列出自查、对方文书分析和双方材料对照三种工作流，并介绍补充权威、反向权威与 KeyCite 信息。它提示我们把文稿审阅组织成有出处的问题清单；本项目没有接入这些专有数据能力。

其[2024 年产品说明](https://www.thomsonreuters.com/en-us/posts/innovation/quick-check-mischaracterization-identification-new-westlaw-enhancement-furthers-the-thomson-reuters-generative-ai-vision/)区分了两层能力：早期引语分析显示逐字增删和上下文；后续功能使用 LLM 解释可能的误述。我们据此区分机械匹配、上下文对照和法律解释，避免一个核验标签混合三者。

| 审阅环节 | 当前实现 | 尚未实现 |
|---|---|---|
| 原文与草稿对照 | 保存精确摘录、显示原文上下文、把摘录与论点关联 | 导入任意诉状后自动识别全部论点、引语和引注 |
| 法律支持关系 | 保存支持/限制/反对关系及解释，供用户审阅 | 自动 LLM 误述识别、对解释正确性的法律评分 |
| 权威与反例 | 保存本会话已有来源及已经链接的反对材料 | KeyCite、全库反例发现、完整后续判例处理状态 |
| 研究持续维护 | 前提、来源或论点更新后，识别依赖失效与重审需要 | 自动决定适用法律变化、自动修复所有受影响分析 |

上述产品信息来自公开帮助与产品说明。关于可取的工作流分层是本项目的设计推论；没有检查付费产品内部代码，也不声称知道其检索、排序或模型提示的内部算法。

## 无外网、无模型调用的最小示例

从仓库根目录使用项目 Python 运行下面代码。它只通过 `Harness._execute` 调用本地工具，不运行模型循环，不发送模型或外部来源请求。材料是虚构测试文本。`_execute` 是内部调试入口，不是稳定的第三方 SDK。

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from core.tool_contracts import Field, Record
from harness.core import Harness
from plugins.research import PLUGIN

base = Path(".chatbox-runtime")
base.mkdir(exist_ok=True)
with TemporaryDirectory(dir=base) as directory:
    h = Harness({"research": PLUGIN}, model="offline",
                config_path=Path(directory) / "harness.json")
    session, _ = h.sessions.start()
    session.loaded.append("research")

    def tool(name, **arguments):
        _, result = h._execute(session, {
            "id": "example", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        })
        if "error" in result.content:
            raise RuntimeError(result.content)
        return result.content

    quote = "The application is dismissed."
    source = session.records.put(Record("case", {
        "full_text": Field(quote, "database", source_id="synthetic:alpha")
    }, "synthetic", "alpha"))
    passage = tool("research__excerpt", ref=source, start=0,
                   end=len(quote), quote=quote)["ref"]
    claim = tool("research__claim", text="The fictional application was dismissed.",
                 status="supported", evidence=[{
                     "excerpt_ref": passage, "relation": "supports"
                 }])["ref"]
    draft = tool("research__draft", title="Synthetic memo", claim_refs=[claim])["ref"]

    # A trusted fixture replaces the source; the old snapshot stays available.
    session.records.put(Record("case", {
        "full_text": Field("The application is allowed.", "database",
                           source_id="synthetic:alpha")
    }, "synthetic", "alpha"), supersedes=source)
    report = tool("research__inspect", ref=draft)
    assert report["stale"] and report["needs_review"]
    assert report["mechanical_binding"] == "intact"
    assert report["legal_verification"] is False
    print([(issue["code"], issue["ref"]) for issue in report["issues"]])
```

实现回归见 `tests/test_research_plugin.py`，覆盖跨作品绑定、原文来源拒绝、源材料/前提变化、复核失效、持久化和长依赖链。四个离线端到端 smoke 场景见 `eval/README.md`；它们检验工作链集成，不衡量模型法律能力。
