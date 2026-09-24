"""McGill Guide citations, rendered from stored records.

The tool takes record numbers only. The citation text is rendered by
``core/mcgill_format`` (the rule file is the single source of templates);
every field used becomes a named leaf of the artifact's derivation, so
"verified" is computed from the derivation chain -- all-database leaves
are verified, and any user-supplied or model-supplied field (a pinpoint
the model typed, a place of publication copied from somewhere else) makes
it honest about being unverified. The model may also write a citation
itself from a record's fields; the reply check then annotates each fact
with the record it came from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Artifact, Derivation, is_grounded
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
        values["pinpoint"] = mcgill_format.mcgill_clean("pinpoint", artifact.content)
        derivation_inputs.append(artifact.derivation)
        derivation_names.append("pinpoint")
    elif p.pinpoint:
        # The value passes through as typed; its provenance is stamped
        # honestly. The user's words keep the citation honest about where
        # the pinpoint came from; a model-typed one is marked model-supplied
        # and the citation reports unverified -- shown, not refused.
        field, _ = ctx.provenance(p.pinpoint)
        values["pinpoint"] = mcgill_format.mcgill_clean("pinpoint", field.value)
        derivation_inputs.append(field)
        derivation_names.append("pinpoint")
    content = mcgill_format.render_fields(record.source_type, values)
    artifact = Artifact("citation_text", content,
                        Derivation(tuple(derivation_inputs), RENDER_RULE, tuple(derivation_names)))
    verified = is_grounded(artifact.derivation)
    ref = ctx.save(artifact, meta={"source_type": record.source_type, "base": None,
                                   "fields": {name: {"value": values.get(name, field.value),
                                                     "origin": field.origin, "source_id": field.source_id}
                                              for name, field in used.items()}})
    state = "Verified" if verified else "Unverified (not every field comes from a database)"
    return Result(
        {"ref": ref, "citation": content, "verified": verified,
         "note": "the citation is stored as an artifact; cite it by ref when building a bibliography"},
        [{"type": "card", "title": "Citation", "rows": [["Citation", content], ["Status", state]],
          "note": "The citation is rendered by rule from the record's fields; the status is derived from "
                  "the fields' sources, never decided by the model."}],
        final=True)


class MissingParams(BaseModel):
    ref: str = PField(min_length=4, max_length=20, description="The record to check, e.g. rec_3")


def missing(ctx, p: MissingParams) -> Result:
    from core import mcgill_format
    record = ctx.records.get(p.ref)
    values = {name: field.value for name, field in record.fields.items()}
    if not mcgill_format.schema_fields(record.source_type):
        raise ValueError(f"This type ({record.source_type}) has no deterministic assembly yet; see the "
                         f"citation plugin's instructions for which types can be rendered.")
    gap = mcgill_format.missing_summary(record.source_type, values)
    missing_fields = mcgill_format.missing_required(record.source_type, values)
    if gap:
        return Result({"ref": p.ref, "missing": gap,
                       "note": "add the values you have with record__compose (base_ref=this record), quoting "
                              "the record or the user's words each came from; if a value is not available, "
                              "tell the user instead of inventing it"},
                      [{"type": "card", "title": "Fields still missing",
                        "rows": [["Record", p.ref], ["Missing", gap],
                                 ["How to add them", "Quote them from a record on screen, or ask the user to type them in"]],
                        "note": "Fields are recorded with their true source; a citation with a non-database "
                                "field is not marked verified."}])
    return Result({"ref": p.ref, "missing": [], "note": "the record has every required field; cite it"},
                  [{"type": "card", "title": "All fields present", "rows": [["Ref", p.ref]],
                    "note": "Every required field is present; the citation can be rendered now."}])


PLUGIN = Plugin(
    name="mcgill",
    title="Citation format (McGill 10th ed.)",
    description="Render a stored record into a McGill citation; verified is computed from the derivation chain.",
    instructions="Citations take record numbers (rec_N), never retyped fields. Rendering with cite is "
                 "preferred: it is deterministic, stamps verified from the derivation, and the artifact "
                 "can join a bibliography. You may also write a citation yourself from a record's fields, "
                 "following the McGill rules -- say which record it came from. When fields are missing, "
                 "add the values you have with record__compose (base_ref=the record), quoting where each "
                 "came from; the citation then reports unverified, which is correct. A web page or file "
                 "record is citable as it is -- cite it directly rather than rebuilding it. If a case has "
                 "no reported citation yet, say so and offer to cite the news report instead.",
    tools=[Tool("cite", "Render a McGill citation from a stored record.", CiteParams, cite),
           Tool("missing", "List the fields a record still needs before it can be cited.", MissingParams, missing)],
    category="function",
    fact_patterns=(r"\[\d{4}\]", r"\b\d{4}\s+[A-Z]{2,6}\s+\d+\b"),
    default_enabled=True,
)