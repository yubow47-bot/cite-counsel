"""McGill Guide citations, rendered from stored records.

The tool takes record numbers only. The citation text is rendered by
``core/mcgill_format`` (the rule file is the single source of templates);
every field used becomes a named leaf of the artifact's derivation, so
"verified" is computed from the derivation chain -- all-database leaves
are verified, any user-supplied field (a pinpoint the user typed, a place
of publication they added) makes it honest about being unverified.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from core.grounding import grounded_reply_guard
from core.tool_contracts import Artifact, Derivation, Field, is_grounded
from harness.plugin import Plugin, Result, Tool, UserText

RENDER_RULE = "cite.render.v1"


class CiteParams(BaseModel):
    ref: str = PField(min_length=4, max_length=20, description="The record to cite, e.g. rec_3")
    pinpoint: UserText = PField(default="", max_length=100,
                                description="A pinpoint the user themselves typed, e.g. at para 64; copied "
                                            "verbatim from the user's messages")
    pinpoint_from: str = PField(default="", max_length=20,
                                description="A pinpoint artifact (art_N) located by the quote plugin")


def cite(ctx, p: CiteParams) -> Result:
    from core import mcgill_format
    record = ctx.records.get(p.ref)
    names = {f["name"] for f in mcgill_format.schema_fields(record.source_type)}
    used = {name: field for name, field in record.fields.items() if name in names}
    values = {name: mcgill_format.mcgill_clean(name, field.value) for name, field in used.items()}
    derivation_inputs, derivation_names = list(used.values()), list(used.keys())
    if p.pinpoint and p.pinpoint_from:
        raise ValueError("Give either the pinpoint the user typed, or the pinpoint from a quote check -- not both.")
    if p.pinpoint_from:
        artifact = ctx.records.get(p.pinpoint_from)
        if not isinstance(artifact, Artifact) or artifact.kind != "pinpoint":
            raise ValueError("pinpoint_from must be a pinpoint artifact from the quote plugin (art_N).")
        values["pinpoint"] = artifact.content
        derivation_inputs.append(artifact.derivation)
        derivation_names.append("pinpoint")
    elif p.pinpoint:
        # The harness verified this against the user's messages and substituted
        # the exact slice; the provenance audit accepts precisely this value.
        values["pinpoint"] = p.pinpoint
        derivation_inputs.append(Field(p.pinpoint, "user"))
        derivation_names.append("pinpoint")
    content = mcgill_format.render_fields(record.source_type, values)
    artifact = Artifact("citation_text", content,
                        Derivation(tuple(derivation_inputs), RENDER_RULE, tuple(derivation_names)))
    verified = is_grounded(artifact.derivation)
    ref = ctx.save(artifact, meta={"source_type": record.source_type, "base": None,
                                   "fields": {name: {"value": values.get(name, field.value),
                                                     "origin": field.origin, "source_id": field.source_id}
                                              for name, field in used.items()}})
    state = "已核验" if verified else "未核验（字段并非全部来自数据库）"
    return Result(
        {"ref": ref, "citation": content, "verified": verified,
         "note": "the citation is stored as an artifact; cite it by ref when building a bibliography"},
        [{"type": "card", "title": "引文", "rows": [["引文", content], ["核验状态", state]],
          "note": "引文由规则从记录字段生成；核验状态由字段来源推导，不由模型判断。"}],
        final=True)


class MissingParams(BaseModel):
    ref: str = PField(min_length=4, max_length=20, description="The record to check, e.g. rec_3")


def missing(ctx, p: MissingParams) -> Result:
    from core import mcgill_format
    record = ctx.records.get(p.ref)
    values = {name: field.value for name, field in record.fields.items()}
    if not mcgill_format.schema_fields(record.source_type):
        raise ValueError(f"此类型（{record.source_type}）尚未支持确定性组装；可以生成引文的类型见引文插件说明。")
    gap = mcgill_format.missing_summary(record.source_type, values)
    missing_fields = mcgill_format.missing_required(record.source_type, values)
    if gap:
        return Result({"ref": p.ref, "missing": gap,
                       "note": "ask the user for these; write them with record__add_user_field"},
                      [{"type": "card", "title": "还缺的字段",
                        "rows": [["记录", p.ref], ["缺少", gap], ["补充方式", "请用户直接在输入框里写出来"]],
                        "note": "补充后的字段标记为你本人的补充，含它的引文不会标记为已核验。"}])
    return Result({"ref": p.ref, "missing": [], "note": "the record has every required field; cite it"},
                  [{"type": "card", "title": "字段齐备", "rows": [["编号", p.ref]],
                    "note": "必需字段齐全，可以直接生成引文。"}])


PLUGIN = Plugin(
    name="mcgill",
    title="引文格式（McGill 第 10 版）",
    description="Render a stored record into a McGill citation; verified is computed from the derivation chain.",
    instructions="Citations take record numbers (rec_N), never retyped fields. When fields are missing, ask the "
                 "user and write them with record__add_user_field -- the citation then reports unverified, which "
                 "is correct. Never write a citation in prose; show the card.",
    tools=[Tool("cite", "Render a McGill citation from a stored record.", CiteParams, cite),
           Tool("missing", "List the fields a record still needs before it can be cited.", MissingParams, missing)],
    category="function",
    fact_patterns=(r"\[\d{4}\]", r"\b\d{4}\s+[A-Z]{2,6}\s+\d+\b"),
    default_enabled=True,
    reply_guard=grounded_reply_guard,
)