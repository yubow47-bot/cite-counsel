"""Check a quotation against the judgment behind a stored case record.

The quotation is whoever supplied it -- the user's own words when they gave
one, otherwise the model's, stamped honestly. The location (and its
paragraph number) is read from the database's full text, so the pinpoint
artifact stays database-grounded either way; only the quotation leaf's
origin differs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Artifact, Derivation
from harness.plugin import Plugin, Result, Tool, UserText

VERDICT_LABEL = {"exact": "Exact match", "case_differs": "Matches, but the case differs",
                 "not_found": "Not found in the text"}


class CheckParams(BaseModel):
    ref: str = PField(min_length=4, max_length=20, description="The case record to check against, e.g. rec_2")
    quote: UserText = PField(min_length=12, max_length=2000,
                             description="The quotation, copied word for word from the user's messages "
                                         "when they gave one")


def check(ctx, p: CheckParams) -> Result:
    from core import quote_check
    record = ctx.records.get(p.ref)
    full = record.fields.get("full_text")
    if full is None or full.origin != "database":
        raise ValueError("This record has no judgment full text for quote checking (a2aj's full_text provides it) -- "
                         "it must be checked against the database's own text.")
    item = {"source_type": record.source_type, "base": None,
            "fields": {name: {"value": field.value, "origin": field.origin, "source_id": field.source_id}
                       for name, field in record.fields.items()}}
    result = quote_check.check(item, p.quote, quoted=ctx.provenance(p.quote)[0],
                               text=full.value, source_id=full.source_id)
    finding_ref = ctx.save(result["finding"])
    pinpoint_ref = ""
    if result["pinpoint"]:
        source = result["finding"].derivation.inputs[1]
        pinpoint_ref = ctx.save(Artifact("pinpoint", result["pinpoint"],
                                         Derivation((source,), "quote.locate.a2aj.v1")),
                                meta={"work": {"provider": record.provider, "record_id": record.record_id,
                                               "source_type": record.source_type}})
    rows = [["Verdict", VERDICT_LABEL.get(result["verdict"], result["verdict"])]]
    if result["pinpoint"]:
        rows.append(["Pinpoint", result["pinpoint"]])
    if result["excerpt"]:
        rows.append(["Excerpt", result["excerpt"]])
    note = ("The pinpoint is taken from the database's own text, so citing it stays verified." if result["pinpoint"]
            else "That sentence is not in the database's full text -- check the quote, or whether it is "
                 "really verbatim from the original.")
    return Result(
        {"finding": finding_ref, "pinpoint_ref": pinpoint_ref, "verdict": result["verdict"],
         "pinpoint": result["pinpoint"],
         "note": "the Finding is stored; pass its pinpoint artifact to mcgill.cite with pinpoint_from"},
        [{"type": "card", "title": "Quote check", "rows": rows, "note": note}], final=True)


PLUGIN = Plugin(
    name="quote",
    title="Quote check",
    description="Check a quotation against the judgment's full text and find its paragraph number.",
    tools=[Tool("check", "Check a quotation against a stored case's full text; locate its paragraph.",
                CheckParams, check)],
    category="function",
    fact_patterns=(r"\bpara(?:s)?\s+\d+",),
    default_enabled=True,
)
