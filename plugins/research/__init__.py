"""Saved, versioned legal assertions, excerpts and drafts.

Artifacts use JSON plus ordinary Derivation/metadata; no legal judgement is
promoted to database evidence. Only the deterministic UI review action can
record a human review, and dependency changes invalidate it at inspection.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field as PField

from core import research
from core.tool_contracts import Artifact, Derivation, Field, Record, grounding_issues
from harness.plugin import Plugin, Result, Tool, UserText

Ref = str


class ExcerptParams(BaseModel):
    ref: Ref = PField(description="Exact session record version, e.g. rec_3; never follows replacements.")
    field: str = PField(default="", max_length=100,
                       description="Source text field. If omitted, select the first available of full_text, text, content; an explicit name must exist.")
    start: int | None = PField(default=None, ge=0, strict=True,
                              description="Inclusive character offset in this exact field. Omit both offsets to locate a uniquely occurring exact quote.")
    end: int | None = PField(default=None, gt=0, strict=True,
                            description="Exclusive character offset, at most 8000 characters after start. Supply both offsets for repeated quotes.")
    quote: str = PField(min_length=1, max_length=8000, description="Exact text expected at these offsets.")
    speaker: Literal["court", "party", "dissent", "quoted_authority", "author", "unknown"] = "unknown"


class EvidenceLink(BaseModel):
    excerpt_ref: Ref
    relation: Literal["supports", "limits", "opposes"]
    note: str = PField(default="", max_length=2000, description="Unverified explanation of the legal relationship.")


class AnalysisScope(BaseModel):
    jurisdiction: str = PField(default="", max_length=300, description="Declared jurisdiction; leave empty when unknown.")
    as_of: str = PField(default="", max_length=100, description="Date or period to which the analysis relates; empty means unknown.")
    procedure: str = PField(default="", max_length=500, description="Procedural setting, if known.")
    factual_context: str = PField(default="", max_length=2000, description="Facts or conditions limiting the assertion.")
    applicability: Literal["unknown", "asserted_applicable", "conditional", "not_applicable"] = "unknown"
    note: str = PField(default="", max_length=2000, description="Unverified explanation of applicability or scope limitations.")


class ClaimParams(BaseModel):
    text: UserText = PField(min_length=1, max_length=2000)
    kind: Literal["claim", "premise", "conclusion"] = "claim"
    status: Literal["unknown", "supported", "disputed"] = "unknown"
    evidence: list[EvidenceLink] = PField(default_factory=list, max_length=40)
    premises: list[Ref] = PField(default_factory=list, max_length=40)
    basis_kind: Literal["source", "assumption", "inference"] = PField(
        default="source", description="source: evidence-based assertion; assumption: explicitly unverified working premise; inference: reasoning from saved premises. All remain legally unverified.")
    reasoning: str = PField(default="", max_length=6000, description="Explain how the saved premises and evidence lead to this assertion, including the inferential step.")
    scope: AnalysisScope = PField(default_factory=AnalysisScope)
    limitations: list[Annotated[str, PField(max_length=2000)]] = PField(default_factory=list, max_length=20, description="Declared limitations or open checks; not an exhaustive checklist.")
    counterargument: str = PField(default="", max_length=4000, description="Strongest identified alternative explanation or counterargument, if any.")
    counterargument_response: str = PField(default="", max_length=4000, description="How the opposing evidence or counterargument is addressed; an assertion, not verification.")
    prior_ref: Ref | None = PField(default=None, description="Previous research object to revise; replaces its links in full.")
    assessment: Literal["pending", "model_assessed"] = "pending"


class DraftParams(BaseModel):
    title: str = PField(min_length=1, max_length=300)
    claim_refs: list[Ref] = PField(min_length=1, max_length=80)
    prior_ref: Ref | None = None


class InspectParams(BaseModel):
    ref: Ref


class CompareParams(BaseModel):
    ref: Ref


class ListParams(BaseModel):
    query: str = PField(default="", max_length=200)
    limit: int = PField(default=30, ge=1, le=100)
    offset: int = PField(default=0, ge=0)


def _object(ctx, ref: str, kinds=None):
    obj = ctx.records.get_exact(ref, Artifact)
    data = research.unpack(obj)
    if kinds is not None and data["type"] not in kinds:
        raise ValueError("The reference is not the required research object type.")
    return obj, data


def _prior(ctx, ref, kind):
    if ref is None:
        return None
    current = ctx.records.current_ref(ref)
    if current != ref:
        raise ValueError(f"Research revision conflict: {ref} was revised to {current}. Read the current version before revising it.")
    _object(ctx, ref, {kind})
    return ref


def _save(ctx, data, inputs, *, prior=None):
    research.validate_revision(ctx.records, prior, data.get("dependencies", []))
    data = {"schema": research.SCHEMA, **data}
    derivation = Derivation(tuple(inputs), "research." + data["type"] + ".v1")
    ref = ctx.save(Artifact("legal_research", json.dumps(data, ensure_ascii=False), derivation),
                   meta={"research": {"type": data["type"], "title": data.get("title", data.get("text", data.get("quote", "")))[:180]},
                         "supersedes": prior}, supersedes=prior)
    return inspect_object(ctx, InspectParams(ref=ref))


def _reference_leaf(dep):
    # References/hashes in the JSON graph are inspected against the session
    # store. They are not source facts and cannot make an assertion grounded.
    # Repeating nested derivations here would copy full documents at every
    # level and make a long research chain grow exponentially on disk.
    return Field(json.dumps(dep, sort_keys=True), "model")


def select_passage(ctx, p: ExcerptParams):
    """Validate a passage without writing; compound tools can preflight all inputs."""
    record = ctx.records.get_exact(p.ref, Record)
    field_name = p.field or next((name for name in ("full_text", "text", "content") if name in record.fields), "")
    field = record.fields.get(field_name)
    if field is None:
        available = ", ".join(sorted(record.fields)) or "none"
        raise ValueError(f"The exact record version has no such source text field. Available fields: {available}.")
    if field.origin == "model":
        raise ValueError("A model-origin field cannot serve as an original source passage.")
    if field.origin == "computed" and (field.derivation is None or grounding_issues(field.derivation)):
        raise ValueError("A computed source field needs a complete auditable derivation before it can be excerpted.")
    if (p.start is None) != (p.end is None):
        raise ValueError("Supply both start and end offsets, or omit both to locate a unique exact quote.")
    start, end = p.start, p.end
    if start is None:
        start = field.value.find(p.quote)
        if start < 0:
            raise ValueError("Quote does not occur in the selected exact record field. Use record__search to find the passage and read its original text.")
        # Search from the following character so overlapping occurrences also
        # count as ambiguous; a guessed first occurrence must not bind a quote.
        if field.value.find(p.quote, start + 1) >= 0:
            raise ValueError("Quote occurs more than once in the selected exact record field. Use record__search and supply both start and end offsets for the intended occurrence.")
        end = start + len(p.quote)
    if end <= start or end > len(field.value) or end - start > 8000:
        raise ValueError("Excerpt offsets must select an existing, nonempty span of at most 8000 characters.")
    if field.value[start:end] != p.quote:
        raise ValueError("Quote does not exactly match the selected record version and character span.")
    return record, field_name, field, start, end


def excerpt(ctx, p: ExcerptParams) -> Result:
    record, field_name, field, start, end = select_passage(ctx, p)
    # Slice provenance remains extracted even when the original database field
    # is grounded. Speaker attribution is a separate, model-supplied assertion.
    source = {"ref": p.ref, "fingerprint": ctx.records.fingerprint(p.ref),
              "work": {"provider": record.provider, "record_id": record.record_id, "source_type": record.source_type},
              "field": field_name, "start": start, "end": end,
              "field_sha256": research.digest(field.value), "quote_sha256": research.digest(p.quote),
              "origin": field.origin, "source_id": field.source_id}
    dep = research.dependency(ctx.records, p.ref, "source")
    # A partial database passage is extraction, not a database fact. A source
    # without a source ID stays mechanically bound in the JSON graph without
    # manufacturing a new source provenance leaf.
    leaf = (Field(p.quote, "extracted", source_id=field.source_id, span=(start, end))
            if field.source_id and field.origin in {"database", "extracted"}
            else _reference_leaf(dep))
    return _save(ctx, {"type": "excerpt", "quote": p.quote, "speaker": p.speaker,
                       "speaker_verification": "asserted_only", "source": source,
                       "dependencies": [dep]}, [leaf, Field(p.speaker, "model")])


def claim(ctx, p: ClaimParams) -> Result:
    if not p.text.strip():
        raise ValueError("A research assertion needs nonempty text.")
    prior = _prior(ctx, p.prior_ref, p.kind)
    dependencies, inputs = [], [Field(str(p.text), "user" if p.text in ctx.claimable_user_values else "model")]
    for link in p.evidence:
        _object(ctx, link.excerpt_ref, {"excerpt"})
        dep = research.dependency(ctx.records, link.excerpt_ref, link.relation)
        dependencies.append(dep)
        inputs.extend([_reference_leaf(dep), Field(link.relation + ": " + link.note, "model")])
    for ref in p.premises:
        _object(ctx, ref, {"premise", "claim", "conclusion"})
        dep = research.dependency(ctx.records, ref, "premise")
        dependencies.append(dep)
        inputs.append(_reference_leaf(dep))
    scope = p.scope.model_dump()
    # Analysis annotations are model assertions, even if some passages or
    # premise text are grounded in a source. Their provenance cannot promote
    # the support relationship or interpretation to verified evidence.
    annotations = {"basis_kind": p.basis_kind, "reasoning": p.reasoning, "scope": scope,
                   "limitations": p.limitations, "counterargument": p.counterargument,
                   "counterargument_response": p.counterargument_response}
    inputs.append(Field(json.dumps(annotations, ensure_ascii=False, sort_keys=True), "model"))
    return _save(ctx, {"type": p.kind, "text": str(p.text), "status": p.status,
                       "evidence": [link.model_dump() for link in p.evidence], "premises": p.premises,
                       "assessment": p.assessment, "semantic_verification": False,
                       "legal_verification": False, "basis_verification": "asserted_only", **annotations,
                       "dependencies": dependencies}, inputs, prior=prior)


def draft(ctx, p: DraftParams) -> Result:
    if not p.title.strip():
        raise ValueError("A draft needs a title.")
    prior = _prior(ctx, p.prior_ref, "draft")
    dependencies, inputs = [], [Field(p.title, "model")]
    for ref in p.claim_refs:
        _, node = _object(ctx, ref, {"claim", "premise", "conclusion"})
        dep = research.dependency(ctx.records, ref, "draft_assertion")
        dependencies.append(dep)
        inputs.append(_reference_leaf(dep))
    lines = [research.render_snapshot(ctx.records, p.title, p.claim_refs)]
    for ref in p.claim_refs:
        report = research.inspect(ctx.records, ref, ctx.state.get("reviews"))
        if report["issues"]:
            lines.extend(["Open checks: " + ", ".join(sorted({issue["code"] for issue in report["issues"]})) + ".", ""])
    return _save(ctx, {"type": "draft", "title": p.title, "claim_refs": p.claim_refs,
                       "markdown": "\n".join(lines), "dependencies": dependencies}, inputs, prior=prior)


def inspect_object(ctx, p: InspectParams) -> Result:
    report = research.inspect(ctx.records, p.ref, ctx.state.get("reviews"))
    report["inspected_at"] = datetime.now(timezone.utc).isoformat()
    return Result(report, [{"type": "research_report", **report}])


def compare(ctx, p: CompareParams) -> Result:
    """Old and new versions of a cited record, side by side. Writes nothing."""
    report = research.compare(ctx.records, p.ref)
    summary = {"ref": report["ref"], "current_ref": report["current_ref"], "newer_version": report["newer_version"],
               "changed_fields": [row["field"] for row in report["fields"]],
               "excerpts": [{key: row[key] for key in ("ref", "is_latest", "new_status", "new_start")}
                            for row in report["excerpts"]],
               "affected": report["affected"][:60], "note": report["note"]}
    return Result(summary, [{"type": "research_compare", **report}])


def list_objects(ctx, p: ListParams) -> Result:
    matches = []
    for ref, obj in reversed(ctx.records.all(Artifact)):
        if obj.kind != "legal_research" or ctx.records.current_ref(ref) != ref:
            continue
        node = research.unpack(obj)
        text = node.get("text", node.get("title", node.get("quote", "")))
        if p.query.casefold() not in text.casefold():
            continue
        matches.append((ref, node, text))
    items = []
    for ref, node, text in matches[p.offset:p.offset + p.limit]:
        report = research.inspect(ctx.records, ref, ctx.state.get("reviews"))
        items.append({"ref": ref, "type": node["type"], "text": text[:180], "stale": report["stale"],
                      "review_status": report["review_status"], "issue_count": len(report["issues"])})
    data = {"items": items, "total": len(matches), "query": p.query, "offset": p.offset,
            "next_offset": p.offset + len(items) if p.offset + len(items) < len(matches) else None}
    return Result(data, [{"type": "research_directory", **data}])


def review_action(ctx, payload):
    ref = InspectParams.model_validate(payload).ref
    report = research.inspect(ctx.records, ref, ctx.state.get("reviews"))
    if report["stale"]:
        raise ValueError("Dependencies changed. Revise the research object against current materials before reviewing it.")
    # This action is absent from model tools. It records the explicit click,
    # while preserving all evidence gaps and asserted legal relationships.
    ctx.state.setdefault("reviews", {})[ref] = {"reviewer": "user", "fingerprint": ctx.records.fingerprint(ref),
                                               "at": datetime.now(timezone.utc).isoformat(),
                                               "scope": "snapshot_acknowledgement; not legal verification"}
    return inspect_object(ctx, InspectParams(ref=ref))


def export_action(ctx, payload):
    """Generate a fresh inspection beside the immutable dependency snapshot."""
    ref = InspectParams.model_validate(payload).ref
    report = research.inspect(ctx.records, ref, ctx.state.get("reviews"))
    timestamp = datetime.now(timezone.utc).isoformat()
    node = report["object"]
    title = node.get("title", "Research object " + ref)
    lines = ["# Export inspection", "", f"Generated at: {timestamp}",
             f"Exact object: {ref}", f"Current version: {report['current_ref']}",
             f"Exact-version fingerprint: {ctx.records.fingerprint(ref)}",
             f"Dependency state: {'STALE — revision required' if report['stale'] else 'No recorded version changes'}",
             f"User acknowledgement: {report['review_status']} (snapshot acknowledgement only; not legal verification)",
             f"Mechanical binding: {report['mechanical_binding']}", "Legal verification: false", "",
             "## Current checks", ""]
    if report["issues"]:
        for issue in report["issues"]:
            lines.append(f"- {issue['code']}: {' → '.join(issue.get('path', [issue['ref']]))}"
                         + (f"; current {issue['current_ref']}" if issue.get("current_ref") else ""))
    else:
        lines.append("No recorded dependency or support-link gaps. Legal interpretation remains unverified.")
    lines.extend(["", report["note"], "", "---", "",
                  research.render_snapshot(ctx.records, title, [ref])])
    exported = {"ref": ref, "current_ref": report["current_ref"], "inspected_at": timestamp,
                "stale": report["stale"], "review_status": report["review_status"],
                "issues": report["issues"], "markdown": "\n".join(lines), "filename": ref + "-inspection.md"}
    return Result({key: value for key, value in exported.items() if key != "markdown"},
                  [{"type": "research_export", **exported}])


PLUGIN = Plugin(
    name="research", title="Research workspace",
    description="Save exact excerpts, assumptions, reasoned claims and drafts; inspect their declared scope, counterevidence, evidence gaps and revisions.",
    tools=[Tool("excerpt", "Save an exact passage bound to a specific record version and work. Omit both offsets for a unique exact quote; repeated quotes require explicit offsets from record__search.", ExcerptParams, excerpt),
           Tool("claim", "Save or revise a source-based assertion, unverified assumption or inference from saved premises. Preserve reasoning, scope, limitations and counterargument response. Does not verify legal meaning.", ClaimParams, claim),
           Tool("draft", "Compose a saved research draft from exact assertion versions, with evidence and open checks.", DraftParams, draft),
           Tool("inspect", "Read an exact research object and inspect missing support, revisions and review state recursively.", InspectParams, inspect_object),
           Tool("list", "Find saved research excerpts, assertions and drafts in this conversation.", ListParams, list_objects),
           Tool("compare", "Compare a cited record version with its newer version: changed text, where each saved excerpt now stands, and which research objects depend on it. Read-only; rebinding is done by saving new excerpts and revising objects.", CompareParams, compare)],
    actions={"list": lambda ctx, payload: list_objects(ctx, ListParams.model_validate(payload)),
             "inspect": lambda ctx, payload: inspect_object(ctx, InspectParams.model_validate(payload)),
             "review": review_action, "export": export_action,
             "compare": lambda ctx, payload: compare(ctx, CompareParams.model_validate(payload))},
    ui=Path(__file__).parent, category="function", default_enabled=True,
    readonly_actions=("list", "inspect", "export", "compare"),
)
