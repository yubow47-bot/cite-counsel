"""Check a quotation against the judgment behind a stored case record.

The quotation must come from the user's own words. The location (and its
paragraph number) is read from the database's full text, so the pinpoint
artifact stays database-grounded even though the quotation is the user's.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Artifact, Derivation
from harness.plugin import Plugin, Result, Tool, UserText

VERDICT_ZH = {"exact": "逐字一致", "case_differs": "词句一致但大小写不同", "not_found": "原文中未找到"}


class CheckParams(BaseModel):
    ref: str = PField(min_length=4, max_length=20, description="The case record to check against, e.g. rec_2")
    quote: UserText = PField(min_length=12, max_length=2000,
                             description="The quotation, copied word for word from the user's messages")


def check(ctx, p: CheckParams) -> Result:
    from core import quote_check
    record = ctx.records.get(p.ref)
    full = record.fields.get("full_text")
    if full is None or full.origin != "database":
        raise ValueError("先取回判决全文（a2aj 的 full_text），再核对引语；核对对象必须是数据库原文。")
    item = {"source_type": record.source_type, "base": None,
            "fields": {name: {"value": field.value, "origin": field.origin, "source_id": field.source_id}
                       for name, field in record.fields.items()}}
    result = quote_check.check(item, p.quote, text=full.value, source_id=full.source_id)
    finding_ref = ctx.save(result["finding"])
    pinpoint_ref = ""
    if result["pinpoint"]:
        source = result["finding"].derivation.inputs[1]
        pinpoint_ref = ctx.save(Artifact("pinpoint", result["pinpoint"],
                                         Derivation((source,), "quote.locate.a2aj.v1")))
    rows = [["结论", VERDICT_ZH.get(result["verdict"], result["verdict"])]]
    if result["pinpoint"]:
        rows.append(["定位引用", result["pinpoint"]])
    if result["excerpt"]:
        rows.append(["原文片段", result["excerpt"]])
    note = ("定位引用取自数据库原文，引用它仍保持已核验。" if result["pinpoint"]
            else "数据库全文里没有这句话；请核对引文，或检查引语是否逐字来自原文。")
    return Result(
        {"finding": finding_ref, "pinpoint_ref": pinpoint_ref, "verdict": result["verdict"],
         "pinpoint": result["pinpoint"],
         "note": "the Finding is stored; pass its pinpoint artifact to mcgill.cite with pinpoint_from"},
        [{"type": "card", "title": "引语核对", "rows": rows, "note": note}], final=True)


PLUGIN = Plugin(
    name="quote",
    title="引语核对",
    description="Check a quotation against the judgment's full text and find its paragraph number.",
    instructions="The quotation must be the user's own words, copied exactly (word for word, punctuation "
                 "included). The verdict and the pinpoint come from the database text, not from you. A "
                 "not_found verdict means the words are not in the text -- say so plainly.",
    tools=[Tool("check", "Check a quotation against a stored case's full text; locate its paragraph.",
                CheckParams, check)],
    category="function",
    fact_patterns=(r"\bpara(?:s)?\s+\d+",),
    default_enabled=True,
)