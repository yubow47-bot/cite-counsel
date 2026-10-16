"""Check stored text and bind locations to the exact work and material version.

The quote and source retain their recorded origins. Extracted files and pages
can be searched without acquiring database verification. Unknown extraction
coverage cannot support a finding of absence from the entire source.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Artifact, Derivation, Record
from core.work_identity import text_field, text_fingerprint, work_identity
from harness.plugin import Plugin, Result, Tool, UserText

VERDICT_LABEL = {"exact": "Exact match", "case_differs": "Matches, but the case differs",
                 "not_found": "Not found in the text"}


class CheckParams(BaseModel):
    ref: str = PField(min_length=4, max_length=20, description="The stored case, file or page to check against, e.g. rec_2")
    quote: UserText = PField(min_length=12, max_length=2000,
                             description="The quotation, copied word for word from the user's messages "
                                         "when they gave one")


def check(ctx, p: CheckParams) -> Result:
    from core import quote_check
    record = ctx.records.get(p.ref, Record)
    material = text_field(record)
    if material is None or material[1].origin not in ("database", "extracted"):
        raise ValueError("This record has no stored full text for quote checking; fetch the judgment "
                         "or extract the file or page first.")
    name, full = material
    source_ref = ctx.records.current_ref(p.ref)
    source_meta = ctx.records.meta(source_ref)
    extraction = source_meta.get("extraction") or {}
    coverage_field = record.fields.get("extraction_coverage")
    if coverage_field:
        try:
            extraction = json.loads(coverage_field.value)
        except (TypeError, ValueError):
            extraction = {}
    if not isinstance(extraction, dict):
        extraction = {}
    coverage = "complete" if (full.origin == "database" or extraction.get("complete") is True
                               or extraction.get("status") == "complete") else "partial"
    binding = {"work": work_identity(record), "text_sha256": text_fingerprint(full.value),
               "text_field": name, "source_ref": source_ref, "source_origin": full.origin}
    item = {"source_type": record.source_type, "base": None,
            "fields": {name: {"value": field.value, "origin": field.origin, "source_id": field.source_id}
                       for name, field in record.fields.items()}}
    result = quote_check.check(item, p.quote, quoted=ctx.provenance(p.quote)[0],
                               text=full.value, source_id=full.source_id,
                               text_origin=full.origin, coverage=coverage)
    finding_ref = ctx.save(result["finding"], meta=binding)
    pinpoint_ref = ""
    if result["pinpoint"]:
        source = result["finding"].derivation.inputs[1]
        pinpoint_ref = ctx.save(Artifact("pinpoint", result["pinpoint"],
                                         Derivation((source,), quote_check.RULE_ID)), meta=binding)
    rows = [["Quotation to check", str(p.quote)],
            ["Verdict", VERDICT_LABEL.get(result["verdict"], result["verdict"])]]
    if result["pinpoint"]:
        rows.append(["Pinpoint", result["pinpoint"]])
    if result["excerpt"]:
        rows.append(["Source excerpt (typography normalized)", result["excerpt"]])
    note = ("The location is bound to this work and stored text version. "
            + ("Its source is database text." if full.origin == "database" else
               "Its source is extracted text; it does not become database verified."))
    if not result["pinpoint"]:
        note += " No numbered paragraph was located." if result["verdict"] != "not_found" else " No match in the stored text."
    if coverage == "partial":
        note += " Extraction coverage is partial or unknown."
    note += " Literal matching does not verify the quotation's legal characterization."
    return Result(
        {"finding": finding_ref, "pinpoint_ref": pinpoint_ref, "verdict": result["verdict"],
         "pinpoint": result["pinpoint"],
         "source_ref": source_ref, "source_origin": full.origin, "coverage": coverage,
         "note": "the Finding is stored; pass its pinpoint artifact to mcgill.cite with pinpoint_from"},
        [{"type": "card", "title": "Quote check", "rows": rows, "note": note}], final=True)


PLUGIN = Plugin(
    name="quote",
    title="Quote check",
    description="Check quotations against stored judgment, file or page text and locate numbered paragraphs.",
    tools=[Tool("check", "Check a quotation against stored source text; preserve its origin and text version.",
                CheckParams, check)],
    category="function",
    fact_patterns=(r"\bpara(?:s)?\s+\d+",),
    default_enabled=True,
)
