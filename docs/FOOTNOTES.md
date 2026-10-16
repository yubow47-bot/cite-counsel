# Footnotes：按脚注顺序生成完整引注、Ibid 和 supra

状态：2026-10-16，第一版。范围：学术模式，判例引注。

## 做什么

`footnotes` 插件保存一篇文档的有序脚注。每条脚注引用本会话里保存的判例记录（精确版本），可带 pinpoint。每次渲染都按当前顺序从头计算编号和形式，所以插入、移动或删除脚注后不会留下过时的 “supra note N”。

| 工具 | 作用 |
|---|---|
| `footnotes__create(title, request_id)` | 新建文档，返回其 ref（也就是当前修订号） |
| `footnotes__edit(document, request_id, op, ...)` | `insert` / `update` / `move` / `delete` 脚注，`short_form` 设简称，`pin_source` 改用被引记录的新版本 |
| `footnotes__render(document)` | 返回编号后的脚注、每项的形式与回指，以及问题清单 |

## 规则

沿用旧项目 `D:\mcgill\core\footnotes.py` 预览器的判断，改为基于本项目的 `mcgill_format.render_fields` 渲染完整引注：

- **Ibid**：当前脚注和上一条脚注都只引一个来源且为同一判例。pinpoint 相同写 `Ibid.`，不同写 `Ibid at …`。上一条有 pinpoint 而这一条引整个判例时，写完整引注，不继承旧 pinpoint。
- **supra**：需要简称，并且总是回指包含该判例首次完整引注的脚注编号，不回指另一条 Ibid 或 supra。
- **简称**：只对在多条脚注里出现的判例建立，写在首次完整引注后，如 `[Gladue]`；不同判例的简称不能相同（忽略大小写）。没有简称的判例重复出现时，写完整引注。
- **pinpoint**：带中立引注的判例只接受段落 pinpoint（`at para …`）；倒序范围拒绝。同一脚注里同一判例只能出现一次。

模板集中在 `core/footnotes.py` 的 `TEMPLATES`，版本号 `footnotes.academic.cases.v1`。这些 Ibid/supra 模板依据 McGill 常见写法，**还没有经过规则审阅**；渲染结果带 `rules_reviewed: false`。

## 修订与重试

文档是会话存储里的版本化对象，创建后保留，不随普通聊天缓存过期。`document_id` 跨修订保持不变；脚注和其中的引用各有稳定 ID，修改同一材料的 pinpoint 或显式更新材料版本时保留引用 ID。旧文档读取时补出稳定身份，不改写旧版本。`edit` 的 `document` 就是这次修改所基于的修订；基于旧修订的修改被拒绝，需先渲染最新修订再改。每次修改带 `request_id`：同一个 id 重试返回第一次的结果，不会重复插入；同一个 id 用于不同内容会被拒绝。请求记账随会话保存，重启后仍可识别重试。

被引记录出现新版本时，渲染结果报告 `newer_source_version`，仍使用文档固定的旧版本；只有 `pin_source` 才改用新版本，并重新检查 pinpoint。

## 可信度标记

每项引注分别报告：`source_verified`（判例字段是否全部来自数据库）、`pinpoint_origin`（`user` 表示用户原话里有这个 pinpoint，`model` 表示模型提供）、`reference_valid`（这处 full/Ibid/supra 及回指在当前文档里成立）。引用形式正确不代表来源已核实。

## 尚未做

法规、二手资料等其他类型；factum 等其他模式；同一脚注内多次引用的细则；导出到 Word。
