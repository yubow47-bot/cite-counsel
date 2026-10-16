"""Footnoted documents: full citation, Ibid and supra worked out from note order.

A document is a versioned artifact in the session store. Each footnote cites
saved case records by their exact version, with an optional pinpoint; short
forms are a per-document display choice. Every render recomputes numbers and
forms from the current order (``core.footnotes``), so inserting, moving or
deleting a note can never leave a stale "supra note N" behind.

Edits name the revision they were made against (the document ref they read)
and carry a ``request_id``: a retried request returns its first result instead of
applying twice, and an edit made against an older revision is refused. A
newer version of a cited record is reported, never substituted; ``pin_source``
moves a document to it explicitly.

First version: academic mode, case citations only. The Ibid/supra templates
in ``core.footnotes`` still need a McGill rules review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as PField

from core import footnotes
from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.plugin import Plugin, Result, Tool

SCHEMA = "footnotes.v1"
CASE_TYPE = "jurisprudence"
MAX_REQUESTS = 200


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ref: str = PField(max_length=20, description="Exact saved case record, e.g. rec_3.")
    pinpoint: str = PField(default="", max_length=100, description="e.g. 'at para 64'; empty cites the whole case.")


class CreateParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = PField(min_length=1, max_length=200)
    request_id: str = PField(min_length=1, max_length=80, description="Unique per intended change; reuse it only to retry the same request.")


class EditParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    document: str = PField(max_length=20, description="The revision this edit is based on: the document ref last returned. An older revision is refused.")
    request_id: str = PField(min_length=1, max_length=80, description="Unique per intended change; reuse it only to retry the same request.")
    op: Literal["insert", "update", "move", "delete", "short_form", "pin_source"]
    note_id: str = PField(default="", max_length=20, description="Existing note for update/move/delete.")
    position: int = PField(default=0, ge=0, le=footnotes.MAX_NOTES,
                           description="1-based position for insert/move; 0 appends (insert).")
    citations: list[Citation] = PField(default_factory=list, max_length=footnotes.MAX_CITATIONS_PER_NOTE,
                                       description="insert/update: the cases this note cites, in order.")
    source: str = PField(default="", max_length=20, description="short_form/pin_source: a cited record ref.")
    short_form: str = PField(default="", max_length=80, description="short_form: label such as 'Gladue'; empty removes it.")


class RenderParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    document: str = PField(max_length=20)


def _unpack(artifact) -> dict:
    if not isinstance(artifact, Artifact) or artifact.kind != "footnote_document":
        raise ValueError("Expected a saved footnote document.")
    data = json.loads(artifact.content)
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported footnote document schema.")
    return data


def _current(ctx, ref: str) -> tuple[str, dict]:
    data = _unpack(ctx.records.get_exact(ref, Artifact))
    current = ctx.records.current_ref(ref)
    if current != ref:
        raise ValueError(f"Revision conflict: the document is now {current}. Render it and redo this edit on that revision.")
    return ref, data


def _source(ctx, ref: str) -> tuple[Record, str]:
    record = ctx.records.get_exact(ref, Record)
    if record.source_type != CASE_TYPE:
        raise ValueError(f"{ref} is a {record.source_type} record; this first version handles case citations only.")
    return record, ctx.records.fingerprint(ref)


def _values(record: Record) -> dict:
    from core import mcgill_format
    names = {f["name"] for f in mcgill_format.schema_fields(CASE_TYPE)} - {"pinpoint"}
    return {name: mcgill_format.mcgill_clean(name, field.value)
            for name, field in record.fields.items() if name in names and field.value}


def _check_pinpoint(record: Record, pinpoint: str) -> str:
    pinpoint = footnotes.clean_pinpoint(pinpoint)
    # Decisions with a neutral citation are cited by paragraph.
    if pinpoint and record.fields.get("neutral_citation") and not pinpoint.lower().startswith(("at para ", "at paras ")):
        raise ValueError("This pinpoint is incompatible with a neutral citation; cite paragraphs, e.g. 'at para 20'.")
    return pinpoint


def _note_citations(ctx, data: dict, citations: list[Citation]) -> list[dict]:
    """Validate a note's citations, registering newly cited records at their exact version."""
    if len({c.ref for c in citations}) != len(citations):
        raise ValueError("Cite each case once per note.")
    result = []
    for citation in citations:
        record, fingerprint = _source(ctx, citation.ref)
        pinned = data["sources"].get(citation.ref)
        if pinned and pinned["fingerprint"] != fingerprint:
            raise ValueError(f"{citation.ref} no longer matches the version this document cites.")
        if not pinned:
            from core import mcgill_format
            mcgill_format.render_fields(CASE_TYPE, _values(record))  # renderable before it is saved
            data["sources"][citation.ref] = {"fingerprint": fingerprint, "short_form": ""}
        pinpoint = _check_pinpoint(record, citation.pinpoint)
        result.append({"source": citation.ref, "pinpoint": pinpoint,
                       "pinpoint_origin": "user" if pinpoint and ctx.user_said(citation.pinpoint) else
                                          ("model" if pinpoint else "")})
    return result


def _index(data: dict, note_id: str) -> int:
    for index, note in enumerate(data["notes"]):
        if note["id"] == note_id:
            return index
    raise ValueError(f"No note {note_id!r} in this document.")


def _apply(ctx, data: dict, p: EditParams) -> None:
    notes = data["notes"]
    if p.op == "insert":
        if not p.citations:
            raise ValueError("insert needs at least one citation.")
        data["next_note"] += 1
        note = {"id": f"n{data['next_note']}", "citations": _note_citations(ctx, data, p.citations)}
        position = p.position or len(notes) + 1
        if position > len(notes) + 1:
            raise ValueError(f"position must be between 1 and {len(notes) + 1}.")
        notes.insert(position - 1, note)
    elif p.op == "update":
        if not p.citations:
            raise ValueError("update needs the note's full list of citations; use delete to remove a note.")
        notes[_index(data, p.note_id)]["citations"] = _note_citations(ctx, data, p.citations)
    elif p.op == "move":
        if not 1 <= p.position <= len(notes):
            raise ValueError(f"position must be between 1 and {len(notes)}.")
        notes.insert(p.position - 1, notes.pop(_index(data, p.note_id)))
    elif p.op == "delete":
        notes.pop(_index(data, p.note_id))
    elif p.op == "short_form":
        if p.source not in data["sources"]:
            raise ValueError("Set a short form only for a case this document cites.")
        label = p.short_form
        if label and (any(c in label for c in "*[]<>") or any(ord(c) < 32 for c in label)):
            raise ValueError("A short form is plain text, e.g. 'Gladue'.")
        others = {footnotes.short_form_key(v["short_form"]) for k, v in data["sources"].items()
                  if k != p.source and v["short_form"]}
        if label and footnotes.short_form_key(label) in others:
            raise ValueError("That short form is already used for another case in this document.")
        data["sources"][p.source]["short_form"] = label
    elif p.op == "pin_source":
        if p.source not in data["sources"]:
            raise ValueError("pin_source applies to a case this document cites.")
        newer = ctx.records.current_ref(p.source)
        if newer == p.source:
            raise ValueError(f"{p.source} is already the latest version.")
        if newer in data["sources"]:
            raise ValueError(f"The newer version {newer} is already cited separately; update those notes instead.")
        record, fingerprint = _source(ctx, newer)
        for note in notes:
            for citation in note["citations"]:
                if citation["source"] == p.source:
                    _check_pinpoint(record, citation["pinpoint"])
                    citation["source"] = newer
        data["sources"][newer] = {**data["sources"].pop(p.source), "fingerprint": fingerprint}
    # Sources no longer cited anywhere are dropped with their short forms.
    cited = {c["source"] for note in notes for c in note["citations"]}
    data["sources"] = {k: v for k, v in data["sources"].items() if k in cited}


def _save(ctx, data: dict, prior: str | None) -> str:
    content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    artifact = Artifact("footnote_document", content, Derivation((Field(content, "model"),), "footnotes.document.v1"))
    return ctx.save(artifact, meta={"footnotes": {"title": data["title"][:180]}}, supersedes=prior)


def _remember(ctx, request_id: str, digest: str, ref: str) -> None:
    requests = ctx.state.setdefault("requests", {})
    requests[request_id] = {"digest": digest, "ref": ref}
    while len(requests) > MAX_REQUESTS:
        requests.pop(next(iter(requests)))


def _replayed(ctx, request_id: str, digest: str) -> str | None:
    seen = ctx.state.get("requests", {}).get(request_id)
    if seen is None:
        return None
    if seen["digest"] != digest:
        raise ValueError("This request_id was already used for a different change; use a new request_id.")
    return seen["ref"]


def _digest(params: BaseModel) -> str:
    return hashlib.sha256(json.dumps(params.model_dump(exclude={"request_id"}), sort_keys=True).encode()).hexdigest()


def render_document(ctx, ref: str) -> dict:
    """Numbers, forms and checks for one exact document revision."""
    from core import mcgill_format
    data = _unpack(ctx.records.get_exact(ref, Artifact))
    records = {key: ctx.records.get_exact(key, Record) for key in data["sources"]}

    def render(source, pinpoint):
        return mcgill_format.render_fields(CASE_TYPE, {**_values(records[source]),
                                                        **({"pinpoint": pinpoint} if pinpoint else {})})

    notes = footnotes.resolve(data["notes"], {k: v["short_form"] for k, v in data["sources"].items() if v["short_form"]},
                              render)
    origins = {(note["id"], c["source"]): c.get("pinpoint_origin", "") for note in data["notes"] for c in note["citations"]}
    issues = []
    for key, pinned in data["sources"].items():
        record = records[key]
        current = ctx.records.current_ref(key)
        if ctx.records.fingerprint(key) != pinned["fingerprint"]:
            issues.append({"code": "source_changed", "source": key})
        if current != key:
            issues.append({"code": "newer_source_version", "source": key, "current_ref": current,
                           "detail": "This document still cites the earlier version. Use pin_source to move to the newer one."})
    for note in notes:
        for citation in note["citations"]:
            record = records[citation["source"]]
            citation["source_verified"] = all(field.origin == "database" for field in record.fields.values()
                                              if field.value)
            citation["pinpoint_origin"] = origins[(note["id"], citation["source"])]
            citation["reference_valid"] = True
    current = ctx.records.current_ref(ref)
    return {"ref": ref, "current_ref": current, "title": data["title"], "mode": "academic",
            "rule_version": footnotes.RULE_VERSION, "rules_reviewed": False,
            "notes": notes, "issues": issues,
            "text": "\n".join(f"{note['number']} {note['text']}" for note in notes)}


def create(ctx, p: CreateParams) -> Result:
    digest = _digest(p)
    existing = _replayed(ctx, p.request_id, digest)
    if existing:
        return Result({"document": existing, "replayed": True})
    ref = _save(ctx, {"schema": SCHEMA, "title": p.title, "mode": "academic", "rule_version": footnotes.RULE_VERSION,
                      "sources": {}, "notes": [], "next_note": 0}, None)
    _remember(ctx, p.request_id, digest, ref)
    return Result({"document": ref, "notes": []})


def edit(ctx, p: EditParams) -> Result:
    digest = _digest(p)
    existing = _replayed(ctx, p.request_id, digest)
    if existing:
        report = render_document(ctx, existing)
        return Result({**report, "document": existing, "replayed": True})
    prior, data = _current(ctx, p.document)
    _apply(ctx, data, p)
    ref = _save(ctx, data, prior)
    _remember(ctx, p.request_id, digest, ref)
    report = render_document(ctx, ref)
    return Result({**report, "document": ref}, [{"type": "footnotes", **report}])


def render(ctx, p: RenderParams) -> Result:
    report = render_document(ctx, p.document)
    return Result(report, [{"type": "footnotes", **report}])


PLUGIN = Plugin(
    name="footnotes", title="Footnotes",
    description="Keep a document's ordered case footnotes and get full citation, Ibid and supra worked out from their order. Academic mode, cases only.",
    tools=[Tool("create", "Start a footnoted document. Returns its ref, which is also its revision.", CreateParams, create),
           Tool("edit", "Insert, update, move or delete a footnote, set a case's short form, or move a cited case to its newer record version. Name the revision you edited and a unique request_id.", EditParams, edit),
           Tool("render", "Show a document revision's numbered footnotes with full/Ibid/supra forms and any issues.", RenderParams, render)],
    actions={"render": lambda ctx, payload: render(ctx, RenderParams.model_validate(payload))},
    ui=Path(__file__).parent, readonly_actions=("render",), requires=("mcgill",), category="function",
    default_enabled=True,
)
