"""McGill-specific record composition, separate from the agent loop."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from core import mcgill_format
from core.evidence_text import find_text, normalize
from core.tool_contracts import Field as EvidenceField, Record
from harness.plugin import Result

# ── Harness-owned record tool (§4.2 of the blueprint) ─────────────────


_RECORD_TYPES = tuple(sorted(mcgill_format.schemas().keys()))
# Types whose citation stands for a decision or an enactment itself; built
# only from news or web text, such a record cites a report, not the source.
_AUTHORITY_TYPES = {"jurisprudence", "legislation", "bill", "treaty", "foreign", "by_law"}


class ComposeField(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="The field name, e.g. author, title, year")
    value: str = Field(min_length=1, max_length=500, description="What goes in the field")
    source: str | None = Field(default=None, max_length=20,
                               description="Where you read it: a record ref (rec_N), or \"user\" for the user's "
                                           "own words. Leave out when it has no source.")
    quote: str | None = Field(default=None, max_length=2000,
                              description="The exact words in that source that contain the value, copied. "
                                          "Leave out when the value is the whole source field.")


class Compose(BaseModel):
    record_type: Literal[_RECORD_TYPES] | None = Field(
        default=None, description="The kind of source, as the citation plugin renders it (jurisprudence for a "
                                  "case, journal_article for an article, website for a web page, constitutional "
                                  "for the Charter, the Constitution Acts or the Canada Act 1982; this type "
                                  "sets the citation form but does not verify the statute text)")
    base_ref: str | None = Field(default=None, max_length=20,
                                 description="An existing record to add fields to or correct; its other fields "
                                             "are kept")
    fields: list[ComposeField] = Field(min_length=1, description="Every field to write, in this one call")

    @model_validator(mode="before")
    @classmethod
    def _fields_by_name(cls, data):
        # Seen live: a model sent a list of fields with no names and the
        # whole call was rejected. The list with an explicit name is the
        # shape; an object keyed by field name is accepted too.
        if isinstance(data, dict) and isinstance(data.get("fields"), dict):
            data = {**data, "fields": [{"name": name, **(item if isinstance(item, dict) else {"value": item})}
                                       for name, item in data["fields"].items()]}
        return data


_FIELD_STATUS = {
    "database": "Verified (traces to the database)",
    "extracted": "Unverified (extracted from a page or file)",
    "user": "Unverified (added by you)",
    "model": "Unverified (written by the model, not checked)",
}


def _evidence(ctx, value: str, source: str | None, quote: str | None,
              name: str = "", record_type: str = "") -> tuple[EvidenceField, str, str]:
    """(field, source ref, why unverified). Database status requires the
    complete corresponding field of the same record type. Bounded text
    occurrences retain an extracted source, without claiming work identity."""
    if not source:
        return EvidenceField(value, "model"), "", "no source was given"
    evidence = quote or value
    if find_text(evidence, value) < 0:
        return EvidenceField(value, "model"), "", "the value is not inside the quote"
    if source.strip().lower() == "user":
        if ctx.user_said(evidence):
            return EvidenceField(value, "user"), "", ""
        return EvidenceField(value, "model"), "", "those words are not in the user's messages"
    try:
        record = ctx.records.get(source, Record)
    except ValueError:
        return EvidenceField(value, "model"), "", f"{source} is not a record in this conversation"
    for field_name, found in record.fields.items():
        if found.origin != "model" and find_text(found.value, evidence) >= 0:
            origin = found.origin
            why = ""
            if origin == "database" and not (
                    field_name == name and record.source_type == record_type
                    and normalize(value) == normalize(found.value)):
                origin = "extracted"
                why = "text occurrence does not verify this field for the cited work"
            return EvidenceField(value, origin, source_id=found.source_id), source, why
    return EvidenceField(value, "model"), "", f"the quote is not in {source}"


def compose(ctx, p: Compose) -> Result:
    if not p.fields:
        raise ValueError("Give at least one field.")
    base = ctx.records.get(p.base_ref, Record) if p.base_ref else None
    record_type = p.record_type or (base.source_type if base else None)
    if not record_type:
        raise ValueError("Give record_type, or base_ref to extend an existing record.")
    if base is not None and p.record_type and p.record_type != base.source_type:
        raise ValueError(f"{p.base_ref} is a {base.source_type} record, not {p.record_type}.")
    fields = dict(base.fields) if base else {}
    written, rejected = {}, {}
    for item in p.fields:
        name = item.name
        problem = mcgill_format.field_problem(record_type, name, item.value)
        if problem:
            rejected[name] = problem
            continue
        value = re.sub(r"\s+", " ", item.value).strip()
        field, from_ref, why = _evidence(ctx, value, item.source, item.quote, name, record_type)
        fields[name] = field
        written[name] = {"value": value, "origin": field.origin, "from_ref": from_ref,
                         **({"unverified_because": why} if why else {})}
    if not written:
        return Result({"error": "nothing was written: every field was rejected", "rejected": rejected},
                      [{"type": "notice", "level": "warning",
                        "text": "No record was written: " + "; ".join(f"{k}: {v}" for k, v in rejected.items())}])
    render_names = {f["name"] for f in mcgill_format.schema_fields(record_type)}
    database_sources = {f.source_id for name, f in fields.items()
                        if name in render_names and f.origin == "database"}
    if len(database_sources) > 1:
        for name, field in list(fields.items()):
            if field.origin == "database":
                fields[name] = replace(field, origin="extracted")
                if name in written:
                    written[name].update(origin="extracted",
                                         unverified_because="fields do not identify one database work")
    record = Record(record_type, fields, base.provider if base else "user", base.record_id if base else "blank")
    ref = ctx.save(record, meta={"composed": True}, supersedes=p.base_ref)
    missing = mcgill_format.missing_summary(record_type, {n: f.value for n, f in fields.items()})
    warnings = []
    if record_type in _AUTHORITY_TYPES and not any(f.origin in ("database", "user") for f in fields.values()):
        sources = sorted({w["from_ref"] for w in written.values() if w["from_ref"]})
        if not sources:
            # Nothing was quoted: point at the pages and files this session holds.
            sources = [r for r, obj in ctx.records.all(Record)
                       if obj.source_type in ("website", "news_online", "document")][-3:]
        warnings.append(
            f"No field of this {record_type} record comes from a database or the user. "
            f"The recorded sources may describe the work without being that work"
            + (f" ({', '.join(sources)})" if sources else "") + ".")
    rows = []
    for name, entry in written.items():
        label = _FIELD_STATUS[entry["origin"]] + (f" — {entry['from_ref']}" if entry["from_ref"] else "")
        rows.append([name, f"{entry['value']}  ·  {label}"])
    rows += [["✕ " + name, reason] for name, reason in rejected.items()]
    if missing:
        rows.append(["Still missing", missing])
    return Result(
        {"ref": ref, "record_type": record_type, "written": written, "rejected": rejected, "missing": missing,
         "warnings": warnings,
         "note": "Rejected fields were not written. Each accepted field retains its recorded origin."},
        [{"type": "card", "title": f"Composed record · {record_type} · {ref}", "rows": rows,
          "note": " ".join(warnings) or "Each field shows where it came from. Citations still render; one with "
                                       "a non-database field is not marked verified."}])
