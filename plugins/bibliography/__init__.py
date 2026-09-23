"""A McGill bibliography from stored citation artifacts.

The tool accepts citation artifact numbers only; the entries come from
the store's sidecar (the exact fields each citation was rendered from),
so an entry's verification status is always re-derived from the field
origins -- and a user-typed pinpoint that the bibliography omits does
not cost the entry its verification.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from harness.plugin import Plugin, Result, Tool


class BuildParams(BaseModel):
    refs: list[str] = PField(min_length=1, max_length=200,
                             description="Citation artifact refs to include, e.g. [\"art_1\", \"art_4\"]")


def build(ctx, p: BuildParams) -> Result:
    from core import bibliography
    entries = []
    for ref in p.refs:
        artifact = ctx.records.get(ref)
        if not artifact or getattr(artifact, "kind", "") != "citation_text":
            raise ValueError(f"{ref} 不是引文成品；请先用引文插件生成。")
        meta = ctx.records.meta(ref)
        if not meta:
            raise ValueError(f"{ref} 没有可用的来源快照，无法编入参考文献。")
        entries.append(meta)
    result = bibliography.build(entries)
    ref = ctx.save(result["artifact"])
    rows = []
    for section in result["sections"]:
        for entry in section["entries"]:
            rows.append([section["title"], ("✓ " if entry["verified"] else "○ ") + entry["text"]])
    return Result(
        {"ref": ref, "count": result["count"], "unverified": result["unverified"],
         "grounded": result["grounded"],
         "note": "the bibliography is stored as an artifact; every entry's verification was re-derived"},
        [{"type": "card", "title": f"参考文献（{result['count']} 条）",
          "rows": rows[:60], "body": result["text"],
          "note": ("全部条目已核验。" if result["unverified"] == 0
                   else f"有 {result['unverified']} 条未核验（○ 标记）：含非数据库来源的字段。")}],
        final=True)


PLUGIN = Plugin(
    name="bibliography",
    title="参考文献",
    description="Assemble stored citations into a McGill bibliography: sections, sorted, pinpoints dropped.",
    instructions="Bibliography takes citation artifact numbers (art_N) from the mcgill plugin. Show the sections "
                 "and the unverified count; the card carries the text to copy.",
    tools=[Tool("build", "Build a McGill bibliography from stored citations.", BuildParams, build)],
    category="function",
    fact_patterns=(),
    default_enabled=True,
)