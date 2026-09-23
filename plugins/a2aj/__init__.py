"""Canadian case law and legislation from A2AJ (a2aj.ca), as records.

Every field the database returns is ``database`` origin with an
``a2aj:…`` source id, so a citation rendered from it can be "verified".
The plugin reports what it searched when nothing matches; it never
concludes that a case does not exist.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.source_tools import SourceContractError, source_cases, source_legislation
from harness.plugin import Plugin, Result, Tool

CASE_FACTS = (r"\b\d{4}\s+[A-Z]{2,6}\s+\d+\b", r"\[\d{4}\]\s*\d*\s*[A-Z]{2,6}\s*\d+")


class CaseParams(BaseModel):
    query: str = Field(min_length=2, max_length=300,
                       description="A case name, or a citation such as 2022 SCC 39 / [1999] 1 SCR 688")


def find_case(ctx, p: CaseParams) -> Result:
    records = source_cases(p.query)
    refs = [_ref(ctx, r) for r in records]
    blocks = [{"type": "record_card", "ref": ref, "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()},
               "verified": True} for ref, r in zip(refs, records)]
    note = "" if records else "。A2AJ 里没有匹配的判例：查询的是按名称与引用号的加拿大判例库，不含美国或英国判例。"
    return Result({"found": len(records), "records": [{"ref": ref} for ref in refs],
                   "note": ("records are stored; refer to them by ref" if records else
                            "no match in the Canadian databases searched; this does not mean it does not exist")},
                  blocks + ([{"type": "notice", "level": "info", "text": "没有找到匹配的判例。" + note[1:]}]
                            if not records else []), final=bool(records))


class LegislationParams(BaseModel):
    query: str = Field(min_length=2, max_length=300, description="A statute name or citation")


def find_legislation(ctx, p: LegislationParams) -> Result:
    records = source_legislation(p.query)
    refs = [_ref(ctx, r) for r in records]
    blocks = [{"type": "record_card", "ref": ref, "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()},
               "verified": True} for ref, r in zip(refs, records)]
    return Result({"found": len(records), "records": [{"ref": ref} for ref in refs],
                   "note": "records are stored; refer to them by ref" if records else "no match"},
                  blocks, final=bool(records))


class FullTextParams(BaseModel):
    ref: str = Field(min_length=4, max_length=20, description="The case record, e.g. rec_2")


def full_text(ctx, p: FullTextParams) -> Result:
    """Fetch the judgment's (unofficial) full text and attach it as a database field."""
    from core import quote_check
    from core.tool_contracts import Field, Record
    record = ctx.records.get(p.ref)
    citation = (record.fields.get("neutral_citation") or record.fields.get("reporter"))
    if citation is None or citation.origin != "database" or not citation.value:
        raise ValueError("只有数据库返回的判例记录能取全文；引用号必须来自数据库字段。")
    found = quote_check.judgment(citation.value)
    if found is None:
        raise ValueError("数据库没有这份判决的全文，无法取回。")
    version = Record(record.source_type,
                     {**record.fields, "full_text": Field(found["text"], "database",
                                                          source_id=found["url"] or f"a2aj:{citation.value}")},
                     record.provider, record.record_id)
    ref = _ref(ctx, version)
    return Result({"ref": ref, "replaces": p.ref, "chars": len(found["text"]),
                   "note": "the full text is a database field on the new record version"},
                  [{"type": "card", "title": "判决全文已取回",
                    "rows": [["记录", ref], ["字符数", str(len(found["text"]))],
                             ["来源", found["url"] or f"a2aj:{citation.value}"]],
                    "note": "全文来自 A2AJ 的非官方文本；引语核对会使用它。"}])


def _ref(ctx, record) -> str:
    return ctx.save(record)


PLUGIN = Plugin(
    name="a2aj",
    title="加拿大判例与法规（A2AJ）",
    description="Look up Canadian cases and legislation by name or citation; fields come from the database "
                "and are traceable.",
    instructions="Records you receive are numbered (rec_N). Cite them by number; never retype their fields. "
                 "When nothing matches, say what was searched -- not that the source does not exist.",
    tools=[Tool("find_case", "Find Canadian cases by name or citation.", CaseParams, find_case),
           Tool("find_legislation", "Find Canadian legislation by name or citation.", LegislationParams,
                find_legislation),
           Tool("full_text", "Fetch the full text of a stored case, from the database.", FullTextParams,
                full_text)],
    category="source",
    fact_patterns=CASE_FACTS,
    default_enabled=True,
)
