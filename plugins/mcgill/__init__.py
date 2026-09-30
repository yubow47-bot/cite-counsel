"""McGill Guide citations, rendered from stored records.

The tool takes a stored record or a registered fixed-form statute title. The citation text is rendered by
``core/mcgill_format`` (the rule file is the single source of templates);
every field used becomes a named leaf of the artifact's derivation, so
"verified" is computed from the derivation chain -- all-database leaves
are verified, and any user-supplied or model-supplied field (a pinpoint
the model typed, a place of publication copied from somewhere else) makes
the artifact unverified. Composition of citation records lives in this
package's ``compose`` module; the harness retains a compatibility registration.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField, model_validator

from core.tool_contracts import Artifact, Derivation, Field, Record, is_grounded
from harness.plugin import Plugin, Result, Tool, UserText

RENDER_RULE = "cite.render.v1"


class CiteParams(BaseModel):
    ref: str = PField(default="", max_length=20, description="The record to cite, e.g. rec_3; omit when giving title.")
    title: str = PField(default="", max_length=200,
                       description="Fixed-form law name: Charter, Constitution Act 1867/1982, or Canada Act 1982. Give title or ref.")
    pinpoint: UserText = PField(default="", max_length=100,
                                description="A pinpoint the user themselves typed, e.g. at para 64; copied "
                                            "verbatim from the user's messages. Leave empty when the user "
                                            "named no provision.")
    pinpoint_from: str = PField(default="", max_length=20,
                                description="A pinpoint artifact (art_N) located by the quote plugin")

    @model_validator(mode="after")
    def target(self):
        if bool(self.ref.strip()) == bool(self.title.strip()):
            raise ValueError("Give exactly one of ref or title.")
        return self


def cite(ctx, p: CiteParams) -> Result:
    from core import mcgill_format
    fixed = mcgill_format.fixed_form(p.title) if p.title else None
    if p.title and fixed is None:
        raise ValueError("No fixed citation form is registered for this title.")
    if fixed:
        said = ctx.user_said(p.title)
        record = Record("legislation", {"title": Field(p.title, "user" if said else "model")},
                        "mcgill_rules", fixed["title"])
    else:
        record = ctx.records.get(p.ref, Record)
    names = {f["name"] for f in mcgill_format.schema_fields(record.source_type)}
    used = {name: field for name, field in record.fields.items() if name in names}
    values = {name: mcgill_format.mcgill_clean(name, field.value) for name, field in used.items()}
    derivation_inputs, derivation_names = list(used.values()), list(used.keys())
    if p.pinpoint and p.pinpoint_from:
        raise ValueError("Give either the pinpoint the user typed, or the pinpoint from a quote check -- not both.")
    added_pinpoint = ""
    if p.pinpoint_from:
        artifact = ctx.records.get(p.pinpoint_from)
        if not isinstance(artifact, Artifact) or artifact.kind != "pinpoint":
            raise ValueError("pinpoint_from must be a pinpoint artifact from the quote plugin (art_N).")
        identity = {"provider": record.provider, "record_id": record.record_id,
                    "source_type": record.source_type}
        if ctx.records.meta(p.pinpoint_from).get("work") != identity:
            raise ValueError("The pinpoint was not located in this cited work.")
        values["pinpoint"] = mcgill_format.mcgill_clean("pinpoint", artifact.content)
        derivation_inputs.append(artifact.derivation)
        derivation_names.append("pinpoint")
    elif p.pinpoint:
        # The value passes through as typed; its provenance is stamped
        # honestly. The user's words keep the citation honest about where
        # the pinpoint came from; a model-typed one is marked model-supplied
        # and the citation reports unverified -- shown, not refused, and
        # said out loud, so it is never passed off as the user's.
        said = ctx.user_said(p.pinpoint)
        field = Field(said or str(p.pinpoint), "user" if said else "model")
        values["pinpoint"] = mcgill_format.mcgill_clean("pinpoint", field.value)
        derivation_inputs.append(field)
        derivation_names.append("pinpoint")
        if field.origin == "model":
            added_pinpoint = values["pinpoint"]
    content = (mcgill_format.render_fixed_form(p.title, values.get("pinpoint", "")) if fixed
               else mcgill_format.render_fields(record.source_type, values))
    artifact = Artifact("citation_text", content,
                        Derivation(tuple(derivation_inputs), RENDER_RULE, tuple(derivation_names)))
    ref = ctx.save(artifact, meta={"source_type": record.source_type,
                                   "base": mcgill_format.render_fixed_form(p.title) if fixed else None,
                                   "fields": {name: {"value": values.get(name, field.value),
                                                     "origin": field.origin, "source_id": field.source_id}
                                              for name, field in used.items()}})
    # From the stored artifact: the store downgrades any leaf it cannot back.
    verified = is_grounded(ctx.records.get(ref).derivation)
    metadata = ctx.records.meta(ref)
    audited = ctx.records.get(ref).derivation
    for name, node in zip(audited.input_names, audited.inputs):
        if name in metadata["fields"] and isinstance(node, Field):
            metadata["fields"][name].update(origin=node.origin, source_id=node.source_id)
    state = "Verified" if verified else "Unverified (not every field comes from a database)"
    rows = [["Citation", content], ["Status", state]]
    reply = {"ref": ref, "citation": content, "verified": verified, "format_checked": True,
             "note": "The citation is stored as an artifact for bibliography inputs."}
    if fixed:
        reply["note"] = "Fixed citation form from the local McGill rules; statute text and pinpoint were not verified."
    if added_pinpoint:
        rows.append(["Pinpoint", f"{added_pinpoint} — added by the assistant; not in your messages"])
        reply["pinpoint_added_by_you"] = (
            f"The user did not supply the pinpoint '{added_pinpoint}'; it was supplied by the assistant.")
    return Result(
        reply,
        [{"type": "card", "title": "Citation", "rows": rows,
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
        raise ValueError(f"This type ({record.source_type}) has no deterministic citation assembly.")
    gap = mcgill_format.missing_summary(record.source_type, values)
    missing_fields = mcgill_format.missing_required(record.source_type, values)
    if gap:
        return Result({"ref": p.ref, "missing": gap,
                       "note": "These fields are needed before this record can be rendered."},
                      [{"type": "card", "title": "Fields still missing",
                        "rows": [["Record", p.ref], ["Missing", gap],
                                 ["Status", "Required fields are absent from this record"]],
                        "note": "Fields are recorded with their true source; a citation with a non-database "
                                "field is not marked verified."}])
    return Result({"ref": p.ref, "missing": [], "note": "The record has every required field for this rendering rule."},
                  [{"type": "card", "title": "All fields present", "rows": [["Ref", p.ref]],
                    "note": "Every required field is present; the citation can be rendered now."}])


PLUGIN = Plugin(
    name="mcgill",
    title="Citation format (McGill 10th ed.)",
    description="Render McGill citations from records or fixed-form law names; report formatting and source status.",
    tools=[Tool("cite", "Render a McGill citation from a stored record or a fixed-form law name such as Charter.", CiteParams, cite),
           Tool("missing", "List the fields a record still needs before it can be cited.", MissingParams, missing)],
    category="function",
    fact_patterns=(r"\[\d{4}\]", r"\b\d{4}\s+[A-Z]{2,6}\s+\d+\b"),
    default_enabled=True,
)
