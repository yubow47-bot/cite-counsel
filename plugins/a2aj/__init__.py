"""Canadian case law and legislation from A2AJ (a2aj.ca), as records.

Every field the database returns is ``database`` origin with an
``a2aj:…`` source id, so a citation rendered from it can be "verified".
The plugin reports what it searched when nothing matches; it never
concludes that a case does not exist.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.grounding import grounded_reply_guard
from core.source_tools import SourceContractError, source_cases, source_legislation
from harness.plugin import Plugin, Result, Tool

CASE_FACTS = (r"\b\d{4}\s+[A-Z]{2,6}\s+\d+\b", r"\[\d{4}\]\s*\d*\s*[A-Z]{2,6}\s*\d+")


class CaseParams(BaseModel):
    query: str = Field(min_length=2, max_length=300,
                       description="A case name, or a citation such as 2022 SCC 39 / [1999] 1 SCR 688")


def find_case(ctx, p: CaseParams) -> Result:
    records = source_cases(p.query)
    blocks = [{"type": "record_card", "ref": _ref(ctx, r), "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()},
               "verified": True} for r in records]
    note = "" if records else "。A2AJ 里没有匹配的判例：查询的是按名称与引用号的加拿大判例库，不含美国或英国判例。"
    return Result({"found": len(records), "records": [{"ref": _ref(ctx, r)} for r in records],
                   "note": ("records are stored; refer to them by ref" if records else
                            "no match in the Canadian databases searched; this does not mean it does not exist")},
                  blocks + ([{"type": "notice", "level": "info", "text": "没有找到匹配的判例。" + note[1:]}]
                            if not records else []), final=bool(records))


class LegislationParams(BaseModel):
    query: str = Field(min_length=2, max_length=300, description="A statute name or citation")


def find_legislation(ctx, p: LegislationParams) -> Result:
    records = source_legislation(p.query)
    blocks = [{"type": "record_card", "ref": _ref(ctx, r), "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()},
               "verified": True} for r in records]
    return Result({"found": len(records), "records": [{"ref": _ref(ctx, r)} for r in records],
                   "note": "records are stored; refer to them by ref" if records else "no match"},
                  blocks, final=bool(records))


def _ref(ctx, record) -> str:
    return ctx.records.put(record)


PLUGIN = Plugin(
    name="a2aj",
    title="加拿大判例与法规（A2AJ）",
    description="Look up Canadian cases and legislation by name or citation; fields come from the database "
                "and are traceable.",
    instructions="Records you receive are numbered (rec_N). Cite them by number; never retype their fields. "
                 "When nothing matches, say what was searched -- not that the source does not exist.",
    tools=[Tool("find_case", "Find Canadian cases by name or citation.", CaseParams, find_case),
           Tool("find_legislation", "Find Canadian legislation by name or citation.", LegislationParams,
                find_legislation)],
    category="source",
    fact_patterns=CASE_FACTS,
    default_enabled=True,
    reply_guard=grounded_reply_guard,
)
