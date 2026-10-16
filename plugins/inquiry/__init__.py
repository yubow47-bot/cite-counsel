"""Open questions, recorded investigation and explicit argument checks.

The inquiry is an evolving, immutable snapshot. Execution receipts come from
the harness; prose claiming that a search happened cannot manufacture one.
Readiness measures recorded work and unresolved conditions, never legal truth.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core import inquiry, receipt_coverage, research
from core.tool_contracts import Artifact, Record
from harness.plugin import CompletionRequest, Plugin, Result, Tool, UserText
from plugins.inquiry.review import review_candidate
from plugins.inquiry.calculations import Calculation, evaluate_calculations, render_calculations
from plugins.inquiry.observations import Observation, evaluate_observations, render_observations
from plugins.inquiry.source_checks import source_report, missing_required


class Scope(BaseModel):
    jurisdiction: str | None = Field(default=None, max_length=500, description="Applicable place or jurisdiction; null means unknown, not worldwide.")
    as_of: str | None = Field(default=None, max_length=200, description="Relevant date or period; null means unknown.")
    procedure: str | None = Field(default=None, max_length=500, description="Procedural or decision context, when relevant.")
    factual_context: str | None = Field(default=None, max_length=2000, description="Known facts and important assumptions; distinguish them in the text.")


class Question(BaseModel):
    id: str | None = Field(default=None, max_length=50, description="Optional short stable identifier for this subquestion.")
    question: str = Field(min_length=1, max_length=1000)
    critical: bool = True
    why: str = Field(default="", max_length=1000, description="How answering this could change the user's decision or a key conclusion.")


class FrameParams(BaseModel):
    question: UserText = Field(default="", description="The question being investigated, preferably in the user's own words. Required for a new inquiry; omit to keep the question when revising.")
    goal: str = Field(default="", max_length=2000, description="The decision or useful outcome the user needs.")
    scope: Scope = Field(default_factory=Scope)
    questions: list[Question] = Field(default_factory=list, max_length=25, description="Only useful subquestions, including important missing facts. No fixed workflow is required.")
    candidate_refs: list[str] = Field(default_factory=list, max_length=40, description="Optional saved research claim, premise or conclusion refs to investigate.")
    prior_ref: str | None = Field(default=None, description="Current inquiry ref to refine after clarification. Only explicitly supplied fields are updated; otherwise start a new inquiry.")


class PursueParams(BaseModel):
    ref: str = Field(description="Current inquiry ref returned by frame or the most recent update.")
    task_id: str = Field(description="Subquestion id from inspect. Use its latest inquiry version.")
    status: Literal["open", "investigating", "resolved", "blocked", "inconclusive"] = "investigating"
    plan: str = Field(default="", max_length=2000, description="A proposed investigation or clarification; a plan is not a completed search.")
    receipt_ids: list[str] = Field(default_factory=list, max_length=30, description="Actual _execution_receipt ids from source, extraction or read calls; inspect also lists recent receipts.")
    findings: str = Field(default="", max_length=4000, description="What those executions establish, fail to establish, or leave uncertain. Interpretation remains model-assessed.")
    next_step: str = Field(default="", max_length=2000, description="The next useful search, comparison, calculation or question for the user.")


class AssessParams(BaseModel):
    ref: str = Field(description="Current inquiry ref.")
    claim_ref: str = Field(description="Exact saved research claim, premise or conclusion ref being checked.")
    rationale: str = Field(min_length=1, max_length=4000, description="Explain whether the cited passages support the actual conclusion, the inference used, and any missing step. This is a model assessment.")
    verdict: Literal["supported", "limited", "disputed", "inconclusive"] = "inconclusive"
    critical: bool = True
    conditions: list[str] = Field(default_factory=list, max_length=25, description="Conditions or exceptions that change applicability or the conclusion.")
    counterevidence_refs: list[str] = Field(default_factory=list, max_length=30, description="Exact saved source excerpts or research claims that challenge or limit the conclusion.")
    response: str = Field(default="", max_length=3000, description="How the counterevidence affects the conclusion; do not silently ignore it.")
    receipt_ids: list[str] = Field(default_factory=list, max_length=30, description="Actual executions used to check applicability or seek counterevidence.")
    scope: Scope | None = None
    premise_ids: list[str] = Field(default_factory=list, max_length=12, description="Stable decisive premise IDs this conclusion depends on. Reassessment binds their current versions.")
    claim_kind: Literal["direct", "inference", "hypothesis"] = "inference"


class AnswerParams(BaseModel):
    ref: str = Field(description="Current inquiry ref.")
    text: str = Field(min_length=1, max_length=16000, description="Useful answer for the user, with options or next steps where relevant. State conditions and unknowns honestly.")
    claim_refs: list[str] = Field(default_factory=list, max_length=60, description="Exact saved claims supporting the important conclusions in this answer.")
    conditions: list[str] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=30, description="Known information or evidence gaps. Saving an answer does not close them.")


class Passage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_ref: str = Field(description="Actual source Record ref, e.g. rec_3, returned by fetch/read. Never a claim or inquiry ref.")
    quote: str = Field(min_length=1, max_length=8000, description="Unique exact passage copied from this source. No character offsets needed.")
    field: str = Field(default="", max_length=100, description="Omit to use text/full_text automatically.")


class PremiseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(default="", max_length=100, description="Existing premise ID to update, or omit for a new premise.")
    statement: str = Field(min_length=1, max_length=1500, description="A specific factual condition whose truth could change the conclusion, not a generic need for more research.")
    if_true: str = Field(min_length=1, max_length=1500, description="How the answer changes IF this condition is true; keep other necessary assumptions explicit.")
    if_false: str = Field(min_length=1, max_length=1500, description="How the answer changes IF this condition is false; failure to prove it does not make it false.")
    next_check: str = Field(min_length=1, max_length=1500, description="A concrete source/query/document passage to investigate, or a precise question only the user can answer.")
    method: Literal["source", "user"] = "source"
    status: Literal["unknown", "supported", "refuted"] = Field(default="unknown", description="Default unknown. Supported/refuted require actual retrieved evidence or an exact user statement; a plausible explanation is still unknown.")
    evidence_refs: list[str] = Field(default_factory=list, max_length=12, description="Exact source Record refs supporting this assessment or containing new material to recheck. Actual execution receipts are bound automatically.")
    user_quote: str = Field(default="", max_length=2000, description="For method=user only: exact text actually supplied by the user, not a model paraphrase.")

    @model_validator(mode="before")
    @classmethod
    def quoted_user_method(cls, value):
        # An omitted discriminator has one unambiguous meaning here. Explicit
        # conflicts still fail, and core still checks the actual user quote.
        if isinstance(value, dict) and value.get("user_quote") and "method" not in value:
            return {**value, "method": "user"}
        return value


class PremiseParams(PremiseSpec):
    ref: str = Field(default="", description="Current inquiry ref. Omit to use this turn's inquiry or begin from the user's question.")


class KeyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conclusion: str = Field(min_length=1, max_length=2000)
    claim_ref: str = Field(default="", description="Existing claim in this inquiry to revise after new evidence. Omit for a new claim; unaffected claims stay unchanged.")
    passages: list[Passage] = Field(min_length=1, max_length=8, description="Prefer 1–3 decisive primary passages actually retrieved (maximum 8); exact quotes, not paraphrases.")
    reasoning: str = Field(min_length=1, max_length=4000, description="Explain the inferential step or calculation and distinguish source facts from user reports and assumptions.")
    objection: str = Field(min_length=1, max_length=2000, description="The strongest relevant exception, alternative explanation or counterargument checked.")
    response: str = Field(default="", max_length=3000, description="What the actual material establishes about that objection and what remains untested; do not claim an exhaustive search. If omitted, the objection is saved as unanswered and the claim cannot be supported.")
    verdict: Literal["supported", "limited", "disputed", "inconclusive"] = "limited"
    task_ids: list[str] = Field(default_factory=list, max_length=10, description="Existing subquestion IDs addressed. Omit for a new inquiry or an inquiry with only one task. Unmentioned tasks remain open.")
    claim_kind: Literal["direct", "inference", "hypothesis"] = Field(default="inference", description="direct: a literal source statement (a paraphrase remains model interpretation); inference: a conclusion needing premises; hypothesis: a possible explanation, never an established fact. Use calculations for arithmetic.")
    premise_ids: list[str] = Field(default_factory=list, max_length=12, description="Existing decisive premise IDs this claim depends on.")
    premises: list[PremiseSpec] = Field(default_factory=list, max_length=3, description="Inline decisive premises, avoiding extra bookkeeping calls. For an inference/hypothesis identify 1–3 concrete conditions that could change it. Unknown conditions stay unknown.")


class ConcludeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=16000, description="Final useful answer in the user's language, including source URLs, conditions and next steps. Delivered directly, without another model round.")
    facts: list[str] = Field(default_factory=list, max_length=3, description="Legacy prose notes, treated as model interpretation. Prefer observations for literal evidence and calculations for arithmetic; these notes are not independently delivered.")
    observations: list[Observation] = Field(default_factory=list, max_length=6, description="Useful exact source/user wording with attribution. No inferred fact summaries.")
    calculations: list[Calculation] = Field(max_length=4, description="Required: for numeric comparisons supply inputs copied from an exact user/source quote. Use [] if there is no calculation. The server computes and always includes the results in the final answer.")
    checks: list[KeyCheck] = Field(min_length=1, max_length=3, description="Only the decisive arguments. Saves exact excerpts, claims and checks in one call; real receipts are bound automatically.")
    ref: str = Field(default="", description="Current inquiry ref if already framed. Omit to use this turn's inquiry or start one from the user's question.")
    jurisdiction: str | None = Field(default=None, max_length=300)
    as_of: str | None = Field(default=None, max_length=100)
    procedure: str | None = Field(default=None, max_length=500)
    factual_context: str | None = Field(default=None, max_length=2000)
    conditions: list[str] = Field(default_factory=list, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20, description="Actual unresolved questions; a conditional answer can still be useful.")


class PassageSelectionError(ValueError):
    """Repair data is actual stored text, never an automatically accepted match."""
    def __init__(self, message, repair):
        super().__init__(message)
        self.repair = repair


class InspectParams(BaseModel):
    ref: str = Field(default="", description="Exact inquiry ref; omit to inspect the active inquiry and recent execution receipts.")


class ListParams(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=20, ge=1, le=60)
    offset: int = Field(default=0, ge=0)


def _receipts(ctx):
    return getattr(ctx.session, "tool_receipts", [])


def _recent_receipts(ctx, limit=12):
    return [{key: row[key] for key in ("id", "tool", "category", "status", "refs", "coverage", "error_kind") if key in row}
            for row in _receipts(ctx) if row.get("category") in {"source", "extract", "read"}][-limit:]


def _ref(ctx, ref=""):
    selected = ref or ctx.session.state.get("inquiry", {}).get("active_ref", "")
    if not selected:
        raise ValueError("No active inquiry. Frame a question first, or supply a saved inquiry ref.")
    inquiry.unpack(ctx.records.get_exact(selected, Artifact))
    return selected


def _turn_key(ctx):
    # Timeline retains all user turns while model history can be trimmed.
    return sum(item.get("role") == "user" for item in ctx.session.timeline)


def _persist(ctx, artifact, prior=None):
    node = inquiry.unpack(artifact)
    baseline = ctx.records.meta(prior).get("material_baseline", 0) if prior else len(_receipts(ctx))
    ref = ctx.save(artifact, meta={"inquiry": {"title": node["question"][:180]}, "supersedes": prior,
                                   "material_baseline": baseline}, supersedes=prior)
    ctx.state["active_ref"] = ref
    ctx.state["touched_turn"] = _turn_key(ctx)
    ctx.session.retained = True
    return ref


def _save(ctx, artifact, prior=None):
    return inspect_object(ctx, InspectParams(ref=_persist(ctx, artifact, prior)))


def frame(ctx, p: FrameParams) -> Result:
    if p.prior_ref:
        prior = _ref(ctx, p.prior_ref)
        patch = {name: getattr(p, name) for name in ("question", "goal", "candidate_refs") if name in p.model_fields_set}
        if "scope" in p.model_fields_set:
            patch["scope"] = p.scope.model_dump(exclude_unset=True)
        if "questions" in p.model_fields_set:
            patch["questions"] = [q.model_dump(exclude_none=True) for q in p.questions]
        return _save(ctx, inquiry.refine(ctx.records, prior, **patch), prior)
    artifact = inquiry.frame(ctx.records, question=str(p.question), goal=p.goal, scope=p.scope.model_dump(),
                             questions=[q.model_dump(exclude_none=True) for q in p.questions], candidate_refs=p.candidate_refs)
    return _save(ctx, artifact)


def pursue(ctx, p: PursueParams) -> Result:
    prior = _ref(ctx, p.ref)
    return _save(ctx, inquiry.pursue(ctx.records, prior, p.task_id, p.status, plan=p.plan,
                                     receipt_ids=p.receipt_ids, findings=p.findings, next_step=p.next_step,
                                     receipts=_receipts(ctx)), prior)


def assess(ctx, p: AssessParams) -> Result:
    prior = _ref(ctx, p.ref)
    if p.claim_kind != "direct" and not p.premise_ids:
        raise ValueError("An inference/hypothesis needs decisive premise_ids. Save its concrete condition with premise first; use direct only for a narrow observed fact or calculation.")
    return _save(ctx, inquiry.assess(ctx.records, prior, p.claim_ref, p.rationale,
                                    conditions=p.conditions, counterevidence_refs=p.counterevidence_refs,
                                    response=p.response, receipt_ids=p.receipt_ids, receipts=_receipts(ctx),
                                    verdict=p.verdict, critical=p.critical,
                                    premise_ids=p.premise_ids, claim_kind=p.claim_kind,
                                    scope=p.scope.model_dump() if p.scope is not None else None), prior)


def _premise_kwargs(ctx, spec):
    """Select trusted acquisition/read receipts; core validates their contents."""
    ids = []
    for ref in spec.evidence_refs:
        matches = receipt_coverage.bound_to(_receipts(ctx), ref, ctx.records.fingerprint(ref),
                                            inquiry.INVESTIGATION_CATEGORIES)
        acquisition = [row for row in matches if row.get("category") in receipt_coverage.ACQUISITION]
        ids.extend(row["id"] for row in (acquisition[-1:] or matches))
    return {**spec.model_dump(exclude={"ref"}), "receipt_ids": list(dict.fromkeys(ids)),
            "receipts": _receipts(ctx), "user_texts": ctx.user_texts()}


def _stage_premises(ctx, prior, initial, scope_patch, checks):
    """Validate compound updates in an isolated store, before any real writes."""
    from harness.records import Store
    shadow = Store()
    shadow.restore([(ref, obj, ctx.records.meta(ref)) for ref, obj in ctx.records.all()])
    stages = []
    def stage(artifact):
        nonlocal prior
        stages.append(artifact)
        prior = shadow.put(artifact, supersedes=prior)
    if initial is not None:
        stage(initial)
    elif scope_patch:
        stage(inquiry.refine(shadow, prior, scope=scope_patch))
    ids_by_check = []
    for check in checks:
        ids = list(check.premise_ids)
        for spec in check.premises:
            artifact = inquiry.premise(shadow, prior, **_premise_kwargs(ctx, spec))
            values = inquiry.unpack(artifact)["premises"]
            identifier = spec.id or values[-1]["id"]
            stage(artifact)
            ids.append(identifier)
        ids = list(dict.fromkeys(ids))
        known = {item["id"] for item in inquiry.unpack(shadow.get_exact(prior, Artifact)).get("premises", [])}
        if set(ids) - known:
            raise ValueError(f"Unknown decisive premise IDs {sorted(set(ids) - known)}; available: {sorted(known)}.")
        if check.claim_kind != "direct" and not ids:
            raise ValueError("An inference/hypothesis needs one concrete decisive premise. Add checks[].premises with statement, if_true, if_false and next_check; use claim_kind=direct only for a narrow observed fact or calculation.")
        ids_by_check.append(ids)
    report = inquiry.inspect(shadow, prior, receipts=_receipts(ctx))
    return stages, ids_by_check, {item["id"]: item for item in report["premises"]}


def premise(ctx, p: PremiseParams) -> Result:
    selected = p.ref or (ctx.state.get("active_ref", "") if ctx.state.get("touched_turn") == _turn_key(ctx) else "")
    prior = _ref(ctx, selected) if selected else None
    initial = None if prior else inquiry.frame(ctx.records, next((text for text in reversed(ctx.user_texts()) if text.strip()), ""))
    # Reuse preflight without inventing a claim or requiring source passages.
    check = KeyCheck.model_construct(premise_ids=[], premises=[p], claim_kind="inference")
    stages, _, _ = _stage_premises(ctx, prior, initial, {}, [check])
    for artifact in stages:
        prior = _persist(ctx, artifact, prior)
    return inspect_object(ctx, InspectParams(ref=prior))


def _source_checks(ctx, node=None, checks=()):
    users = ctx.user_texts()
    texts = ([node["question"]] if node else []) + users[-1:]
    premises = list((node or {}).get("premises", []))
    premises += [value for check in checks for value in check.premises]
    return source_report(ctx.records, _receipts(ctx), user_texts=texts,
                         premises=premises, messages=ctx.session.messages)


def _pending_calculations(ctx):
    pending = ctx.state.get("pending_calculations") or {}
    if pending.get("turn") != _turn_key(ctx):
        return []
    try:
        for spec in pending.get("specs", []):
            ref = spec.get("source_ref")
            if ref and ctx.records.current_ref(ref) != ref:
                return []
        return evaluate_calculations(ctx, pending.get("specs", []))
    except ValueError:
        return []


def _review_contexts(ctx, contexts):
    """Include already obtained surrounding material, not just chosen quotes.

    A candidate can select a general rule while missing its nearby exception.
    Supplement with bounded source openings and actual read/search spans; no
    additional fetch is hidden inside review.
    """
    result, seen = [], set()
    for item in contexts:
        key = (item["ref"], item["field"], item.get("offset", 0), item["text"])
        if key not in seen:
            result.append(item)
            seen.add(key)
    refs = list(dict.fromkeys(item["ref"] for item in contexts))[:4]
    for ref in refs:
        record = ctx.records.get_exact(ref, Record)
        name = next((key for key in ("full_text", "text", "content") if key in record.fields), "")
        field = record.fields.get(name)
        if field is None or field.origin not in {"database", "extracted", "user"}:
            continue
        spans = [(0, min(len(field.value), 4000))]
        fingerprint = ctx.records.fingerprint(ref)
        for row in _receipts(ctx):
            if row.get("status") != "ok" or row.get("executed") is False or not any(
                    value.get("ref") == ref and value.get("fingerprint") == fingerprint for value in row.get("refs", [])):
                continue
            for observed in row.get("observations", []):
                start, end = observed.get("offset"), observed.get("end")
                if (observed.get("ref") == ref and observed.get("field") == name
                        and type(start) is int and type(end) is int and 0 <= start < end <= len(field.value)
                        and research.digest(field.value[start:end]) == observed.get("text_sha256")):
                    spans.append((max(0, start - 400), min(len(field.value), end + 400)))
        # Merge overlapping excerpts rather than repeatedly quoting headers.
        merged = []
        for start, end in sorted(set(spans)):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        # Only supplement text the selected contexts do not already contain.
        covered = [(item.get("offset", 0), item.get("offset", 0) + len(item["text"]))
                   for item in result if item["ref"] == ref and item["field"] == name]
        uncovered = []
        for start, end in merged:
            cursor = start
            for left, right in sorted(covered):
                if right <= cursor or left >= end:
                    continue
                if left > cursor:
                    uncovered.append((cursor, left))
                cursor = max(cursor, right)
            if cursor < end:
                uncovered.append((cursor, end))
        budget = 5000
        for start, end in uncovered:
            if budget <= 0:
                break
            text = field.value[start:min(end, start + budget)]
            result.append({"ref": ref, "field": name, "offset": start, "text": text,
                           "purpose": "surrounding_source_and_observed_context", "truncated": len(text) < end - start})
            budget -= len(text)
        url = record.fields.get("url")
        if url is not None and url.origin in {"database", "extracted"}:
            for item in result:
                if item["ref"] == ref:
                    item["source_url"] = url.value
    return result


def _conditional_answer(text, checks, premises, facts=(), *, review=None, observations=(), calculations=()):
    """Only a review bound to this exact prose may release model interpretation.

    Literal quotations establish attribution; computed results establish arithmetic.
    Neither a direct label nor a free-form facts field establishes truth.
    """
    zh = any("一" <= ch <= "鿿" for ch in text)
    reviewed = (review or {}).get("status") == "model_reviewed" and (review or {}).get("reviewed_answer_sha256") == research.digest(text)
    unresolved = []
    seen = set()
    for row in checks:
        for identifier in row.get("premise_ids", []):
            value = premises[identifier]
            if identifier not in seen and (value["effective_status"] != "supported" or row.get("needs_recheck")):
                unresolved.append(value)
                seen.add(identifier)
    gaps = [(value["statement"] + ": " + value["next_check"])[:2000] for value in unresolved]
    if reviewed:
        lines = [text]
    else:
        lines = ["本次未完成答复复核。以下保留已取得的原文和可复算结果；尚不能据此确认推论。" if zh else
                 "The answer review did not complete. The attributed material and reproducible calculations below remain available; the proposed conclusions are not confirmed."]
        observations_text = render_observations(observations, zh)
        if observations_text:
            lines += [observations_text]
        if unresolved:
            lines += ["仍需查明：" if zh else "Still to establish:"]
            for value in unresolved:
                lines += [("- 待核前提：" if zh else "- Premise to check: ") + value["statement"],
                          ("  下一步：" if zh else "  Next check: ") + value["next_check"]]
    computed = render_calculations(calculations, zh)
    if computed:
        lines += [computed]
    return "\n\n".join(lines), gaps


def answer(ctx, p: AnswerParams) -> Result:
    prior = _ref(ctx, p.ref)
    report = inquiry.inspect(ctx.records, prior, receipts=_receipts(ctx))
    claims = p.claim_refs or (report["object"].get("answer") or {}).get("claim_refs") or report["object"]["candidate_refs"]
    by_ref = {row["claim_ref"]: row for row in report["assessments"]}
    checks = []
    for claim_ref in claims:
        row = by_ref.get(claim_ref, {"needs_recheck": True})
        checks.append({**row, "conclusion": research.unpack(ctx.records.get_exact(claim_ref, Artifact))["text"],
                       "claim_kind": row.get("claim_kind", "inference"), "premise_ids": row.get("premise_ids", [])})
    saved = report["object"].get("answer") or {}
    evidence = saved.get("delivery_evidence") or {}
    # No model-facing shortcut around conclude. Internal callers can safely
    # replay a current saved answer, but changed prose needs a fresh review.
    if saved.get("text") == p.text and not report["answer_needs_refresh"]:
        return Result({"ref": prior, "answer_delivered": True}, final=True, reply=p.text)
    text, gaps = _conditional_answer(p.text, checks, {value["id"]: value for value in report["premises"]},
                                    observations=evidence.get("observations", []) if not report["answer_needs_refresh"] else [],
                                    calculations=evidence.get("calculations", []) if not report["answer_needs_refresh"] else [])
    result = _save(ctx, inquiry.answer(ctx.records, prior, text, claim_refs=claims,
                                     conditions=p.conditions, gaps=list(dict.fromkeys(p.gaps + gaps)), receipts=_receipts(ctx)), prior)
    result.final, result.reply = True, result.content["object"]["answer"]["text"]
    return result


def _passage_repair(ctx, passage):
    """Actual source text near a failed quote, so the model can copy a contiguous one."""
    record = ctx.records.get_exact(passage.source_ref)
    if not isinstance(record, Record):
        return None
    name = passage.field or next((key for key in ("full_text", "text", "content") if key in record.fields), "")
    original = record.fields.get(name)
    if original is None or not isinstance(original.value, str) or original.origin == "model":
        return None
    fragments = re.split(r"\.{3}|…", passage.quote)
    anchors = [original.value.find(part.strip()[:80]) for part in fragments if len(part.strip()) >= 16]
    at = next((at for at in anchors if at >= 0), 0)
    left = max(0, at - 150)
    return {"source_ref": passage.source_ref, "field": name, "offset": left,
            "text": original.value[left:left + 2000],
            "next_step": "Retry conclude with a shorter contiguous exact quote copied from this text. Do not join fragments with ellipses. Nothing from this failed call was saved."}


def _bind_passage(ctx, rc, index, passage):
    """Locate one exact quote and the real receipts that obtained or read it.

    Returns the excerpt params, receipt IDs and a review context; writes nothing.
    """
    from plugins import research as research_plugin
    try:
        ctx.records.get_exact(passage.source_ref, Record)
    except (ValueError, TypeError) as exc:
        refs = [ref for ref, _ in ctx.records.all(Record)][-12:]
        raise PassageSelectionError(
            f"checks[{index}].passages source_ref={passage.source_ref!r} is not a source Record. "
            f"Available source refs: {refs}. Put user wording in observations/calculations with source_ref omitted. "
            "Keep checks for arguments with actual retrieved source passages; do not create a source ref named 'user'.",
            {"available_source_refs": refs, "next_step": "Remove the user-only check; the separate calculations already preserve its numeric results. Keep 1-2 sourced checks with exact contiguous quotes."}) from exc
    params = research_plugin.ExcerptParams(ref=passage.source_ref, field=passage.field, quote=passage.quote)
    try:
        _, field_name, field, start, end = research_plugin.select_passage(rc, params)
    except ValueError as exc:
        raise PassageSelectionError(f"checks[{index}], source {passage.source_ref}: {exc}",
                                    _passage_repair(ctx, passage)) from exc
    matches = receipt_coverage.bound_to(_receipts(ctx), passage.source_ref,
                                        ctx.records.fingerprint(passage.source_ref), inquiry.INVESTIGATION_CATEGORIES)
    # A read proves inspection only of its returned field and span. Keep older
    # relevant reads; adjacent page reads can jointly cover one exact excerpt.
    spans = receipt_coverage.read_spans(matches, passage.source_ref, field_name, field.value, start, end)
    receipt_ids = receipt_coverage.covering_reads(spans, start, end)
    if receipt_ids is None:
        # Retrieval/extraction receipts establish acquisition of this exact
        # material version, without claiming passage inspection.
        retrieved = [row for row in matches if row.get("category") in receipt_coverage.ACQUISITION]
        if not retrieved:
            raise ValueError(f"No successful source retrieval or complete passage read is bound to {passage.source_ref}, "
                             f"field {field_name}, span {start}:{end}. Use record__read with ref={passage.source_ref}, "
                             f"field={field_name}, offset={start}, limit={min(end - start, 4000)} "
                             "and continue at next_offset if needed. Saved text or an unrelated read is not passage inspection.")
        receipt_ids = [retrieved[-1]["id"]]
    left, right = max(0, start - 700), min(len(field.value), end + 700)
    context = {"check": index, "ref": passage.source_ref, "field": field_name,
               "text": field.value[left:right][:6000], "offset": left, "truncated": right - left > 6000}
    return params, receipt_ids, context


def _bind_checks(ctx, p, node, tasks):
    """Validate every check against the inquiry and real receipts before any write."""
    _, rc = ctx.use("research")
    task_ids = {item["id"] for item in tasks}
    prepared, contexts, revised_claims = [], [], set()
    for index, check in enumerate(p.checks, 1):
        if check.claim_ref:
            if not node or check.claim_ref not in node["candidate_refs"] or check.claim_ref in revised_claims:
                raise ValueError("claim_ref must identify a distinct existing claim in this inquiry.")
            if ctx.records.current_ref(check.claim_ref) != check.claim_ref:
                raise ValueError(f"Claim revision conflict: use current ref {ctx.records.current_ref(check.claim_ref)}.")
            revised_claims.add(check.claim_ref)
        addressed = check.task_ids or ([tasks[0]["id"]] if len(tasks) == 1 else [])
        unknown = set(addressed) - task_ids
        if unknown:
            raise ValueError(f"checks[{index}].task_ids has unknown IDs {sorted(unknown)}; available: {sorted(task_ids)}.")
        passages, receipt_ids = [], []
        for passage in check.passages:
            params, ids, context = _bind_passage(ctx, rc, index, passage)
            passages.append(params)
            receipt_ids.extend(ids)
            contexts.append(context)
        receipt_ids = list(dict.fromkeys(receipt_ids))
        inquiry._receipt_bindings(ctx.records, receipt_ids, _receipts(ctx))
        prepared.append((check, passages, receipt_ids, addressed))
    return rc, prepared, contexts


def _default_observations(ctx, p, prepared):
    observations = evaluate_observations(ctx, p.observations)
    if not observations:
        # Preserve decisive source wording if no explicit observation was supplied.
        for _, passages, _, _ in prepared:
            for passage in passages:
                if len(observations) < 3 and len(passage.quote) <= 1500:
                    observations.extend(evaluate_observations(ctx, [Observation(
                        source_ref=passage.ref, quote=passage.quote, field=passage.field)]))
    return observations


def _source_request(ctx, sources, missing):
    """Ask once per turn for named sources not yet fetched, if time allows."""
    unvisited = [row for row in missing if row["status"] == "unvisited"]
    runtime = ctx.session.runtime
    can_investigate = runtime is None or (runtime.phase == "investigation" and runtime.remaining() > 35 and runtime.can_call())
    if not (unvisited and ctx.is_enabled("web") and can_investigate
            and ctx.state.get("source_request_turn") != _turn_key(ctx)):
        return None
    ctx.state["source_request_turn"] = _turn_key(ctx)
    return Result({"status": "needs_sources", "source_checks": sources,
                   "next_step": "Read these supplied sources before finalizing, then call conclude once. A failed fetch should be reported honestly, not retried indefinitely.",
                   "suggested_calls": [{"name": "web__fetch", "arguments": {"url": row["url"]}} for row in unvisited]})


def _reviewed(ctx, p, contexts, evidence, prior, initial, scope_patch):
    """One bounded review, then premise staging; an invalid review falls back to the draft."""
    supplied_review = ctx.completion_review
    original = p
    if supplied_review:
        review = {**supplied_review, "reviewed_answer_sha256": research.digest(p.answer)}
    else:
        p, review = review_candidate(ctx, p, _review_contexts(ctx, contexts), delivery_evidence=evidence)
    try:
        staged = _stage_premises(ctx, prior, initial, scope_patch, p.checks)
    except ValueError:
        # A malformed review cannot consume the budget and strand a valid
        # candidate, or authorize its unreviewed prose. Preserve safe evidence.
        p = original
        review = {**review, "status": "failed", "reason": "invalid_review_premises"}
        review.pop("reviewed_answer_sha256", None)
        staged = _stage_premises(ctx, prior, initial, scope_patch, p.checks)
    return p, review, staged


def _source_links(ctx, prepared, delivered, answer_text):
    links = []
    for source_ref in dict.fromkeys(passage.ref for _, passages, _, _ in prepared for passage in passages):
        url = ctx.records.get_exact(source_ref, Record).fields.get("url")
        value = url.value if url else ""
        if isinstance(value, str) and value.startswith(("https://", "http://")) and value not in delivered:
            links.append(f"- [{source_ref}]({value.replace('(', '%28').replace(')', '%29')})")
    if not links:
        return ""
    label = "来源" if any("一" <= ch <= "鿿" for ch in answer_text) else "Sources"
    return "\n\n" + label + ":\n\n" + "\n".join(links)


def _delivery(ctx, p, review, premise_ids, premise_states, observations, calculations, missing, prepared):
    """The exact text to deliver, its gaps, the effective verdicts and the review record."""
    safe_checks = [{"conclusion": check.conclusion, "claim_kind": check.claim_kind, "premise_ids": ids}
                   for check, ids in zip(p.checks, premise_ids)]
    inquiry._strings(p.conditions, "conditions")
    inquiry._strings(p.facts, "facts")
    delivered, premise_gaps = _conditional_answer(p.answer, safe_checks, premise_states, review=review,
                                                  observations=observations, calculations=calculations)
    source_gaps = [f"{row['url']}: " + ("retrieval failed" if row["status"] == "failed" else "not retrieved") for row in missing]
    if source_gaps:
        zh = any("一" <= ch <= "鿿" for ch in p.answer)
        delivered += "\n\n" + ("尚未取得的指定来源（相关结论需保留）：" if zh else "Requested sources still unavailable (related conclusions remain provisional):") + "\n\n" + "\n".join("- " + gap for gap in source_gaps)
    effective_verdicts = ["limited" if check.verdict == "supported" and (
        check.claim_kind == "hypothesis" or not check.response.strip()
        or any(premise_states[identifier]["effective_status"] != "supported" for identifier in ids))
        else check.verdict for check, ids in zip(p.checks, premise_ids)]
    # An omitted response is accepted, not invented: the objection stays open.
    unanswered = [("未回应的反论点：" if any("一" <= ch <= "鿿" for ch in p.answer) else "Objection not yet answered: ")
                  + check.objection for check in p.checks if not check.response.strip()]
    gaps = list(dict.fromkeys(p.gaps + premise_gaps + source_gaps + unanswered))
    inquiry._strings(gaps, "gaps")
    if delivered != p.answer:
        review = {**review, "conditional_gate": True, "draft_sha256": research.digest(p.answer),
                  "corrections": [*review.get("corrections", []), "Bound delivery to a completed prose review and independently preserved validated evidence/calculations."]}
    delivered += _source_links(ctx, prepared, delivered, p.answer)
    if len(delivered) > 24000:
        raise ValueError("Answer with source links exceeds 24000 characters; shorten the answer or use fewer decisive passages.")
    return delivered, gaps, effective_verdicts, review


def _save_checks(ctx, rc, p, prior, prepared, effective_verdicts, premise_ids, premise_states, scope, gaps):
    """Write excerpts, claims, assessments and task updates; returns the new inquiry ref and claims."""
    from plugins import research as research_plugin
    claims, updates = [], {}
    for index, (check, passages, receipt_ids, addressed) in enumerate(prepared):
        verdict = effective_verdicts[index]
        excerpts = [research_plugin.excerpt(rc, params).content["ref"] for params in passages]
        claim_scope = {**{key: value or "" for key, value in scope.items()},
                       "applicability": "conditional" if p.conditions or gaps or verdict != "supported" else "asserted_applicable"}
        claim_ref = research_plugin.claim(rc, research_plugin.ClaimParams(
            text=check.conclusion, status="disputed" if verdict == "disputed" else "supported" if verdict == "supported" else "unknown",
            evidence=[{"excerpt_ref": ref, "relation": "supports"} for ref in excerpts],
            reasoning=check.reasoning, scope=claim_scope, limitations=p.conditions,
            counterargument=check.objection, counterargument_response=check.response, assessment="model_assessed",
            kind=research.unpack(ctx.records.get_exact(check.claim_ref, Artifact))["type"] if check.claim_ref else "claim",
            prior_ref=check.claim_ref or None)).content["ref"]
        if check.claim_ref:
            current = inquiry.unpack(ctx.records.get_exact(prior, Artifact))
            prior = _persist(ctx, inquiry.refine(ctx.records, prior, candidate_refs=[
                ref for ref in current["candidate_refs"] if ref != check.claim_ref]), prior)
        claims.append(claim_ref)
        prior = _persist(ctx, inquiry.assess(ctx.records, prior, claim_ref, check.reasoning,
                         verdict=check.verdict, conditions=p.conditions, response=check.response,
                         premise_ids=premise_ids[index], claim_kind=check.claim_kind,
                         receipt_ids=receipt_ids, receipts=_receipts(ctx), scope=scope), prior)
        for task_id in addressed:
            pending = check.claim_kind == "hypothesis" or any(premise_states[identifier]["effective_status"] != "supported" for identifier in premise_ids[index])
            updates.setdefault(task_id, []).append((check, receipt_ids, verdict, pending))
    for task_id, checks in updates.items():
        resolved = all(verdict in {"supported", "limited"} and not pending for _, _, verdict, pending in checks)
        findings = "\n".join(check.conclusion + " " + check.reasoning for check, _, _, _ in checks)[:8000]
        prior = _persist(ctx, inquiry.pursue(ctx.records, prior, task_id,
                         "resolved" if resolved else "inconclusive", findings=findings,
                         receipt_ids=list(dict.fromkeys(identifier for _, ids, _, _ in checks for identifier in ids)),
                         next_step="; ".join(gaps)[:4000], receipts=_receipts(ctx)), prior)
    return prior, claims


def conclude(ctx, p: ConcludeParams) -> Result:
    """A short completion path using the same evidence and inquiry contracts.

    Validate all passages, task IDs and real execution bindings before writing.
    This bundles bookkeeping, not source selection or semantic judgement.
    """
    from plugins import research as research_plugin

    selected = p.ref
    if not selected and ctx.state.get("touched_turn") == _turn_key(ctx):
        selected = ctx.state.get("active_ref", "")
    prior = _ref(ctx, selected) if selected else None
    node = inquiry.unpack(ctx.records.get_exact(prior, Artifact)) if prior else None
    if prior and ctx.records.current_ref(prior) != prior:
        raise ValueError(f"Inquiry revision conflict: use current ref {ctx.records.current_ref(prior)}.")
    scope = dict(node["scope"]) if node else dict.fromkeys(inquiry.SCOPE_FIELDS)
    scope_patch = {key: getattr(p, key) for key in inquiry.SCOPE_FIELDS if key in p.model_fields_set}
    scope.update(scope_patch)
    question = node["question"] if node else next((text for text in reversed(ctx.user_texts()) if text.strip()), "")
    calculations = evaluate_calculations(ctx, p.calculations)
    # Retain independently valid work if a later claim/quote needs repair.
    # These are specs, revalidated against actual inputs before any reuse.
    if p.calculations:
        ctx.state["pending_calculations"] = {"turn": _turn_key(ctx),
            "specs": [item.model_dump(mode="json") for item in p.calculations]}
    else:
        calculations = _pending_calculations(ctx)
    # Pure validation also checks answer strings before any compound write.
    inquiry._strings(p.conditions, "conditions")
    inquiry._strings(p.gaps, "gaps")
    inquiry._strings(p.facts, "facts")
    research_plugin.AnalysisScope.model_validate({key: value or "" for key, value in scope.items()})
    initial = inquiry.frame(ctx.records, question, scope=scope) if node is None else None
    tasks = node["tasks"] if node else inquiry.unpack(initial)["tasks"]
    rc, prepared, contexts = _bind_checks(ctx, p, node, tasks)

    # Parameter repair should not consume the one model-review allowance.
    _stage_premises(ctx, prior, initial, scope_patch, p.checks)
    observations = _default_observations(ctx, p, prepared)
    sources = _source_checks(ctx, node, p.checks)
    missing = missing_required(sources)
    request = _source_request(ctx, sources, missing)
    if request is not None:
        return request
    evidence = {"observations": observations, "calculations": calculations, "source_checks": sources,
                "source_checks_scope": "explicitly_requested_urls_only; additional acquired sources appear in source_contexts"}
    p, review, (stages, premise_ids, premise_states) = _reviewed(ctx, p, contexts, evidence, prior, initial, scope_patch)
    # The review may rewrite check text; passages and receipts stay as bound.
    prepared = [(p.checks[index], passages, receipt_ids, addressed)
                for index, (_, passages, receipt_ids, addressed) in enumerate(prepared)]
    delivered, gaps, effective_verdicts, review = _delivery(
        ctx, p, review, premise_ids, premise_states, observations, calculations, missing, prepared)

    for artifact in stages:
        prior = _persist(ctx, artifact, prior)
    prior, claims = _save_checks(ctx, rc, p, prior, prepared, effective_verdicts, premise_ids, premise_states, scope, gaps)
    final = _save(ctx, inquiry.answer(ctx.records, prior, delivered, claim_refs=claims,
                  conditions=p.conditions, gaps=gaps, receipts=_receipts(ctx), review=review, delivery_evidence=evidence), prior)
    final.content = {"ref": final.content["ref"], "readiness": final.content["readiness"],
                     "blockers": final.content["blockers"], "claim_refs": claims,
                     "answer_delivered": True, "semantic_verification": False}
    final.final, final.reply = True, delivered
    return final


def inspect_object(ctx, p: InspectParams) -> Result:
    ref = _ref(ctx, p.ref)
    report = inquiry.inspect(ctx.records, ref, receipts=_receipts(ctx))
    report["inspected_at"] = datetime.now(timezone.utc).isoformat()
    report["recent_receipts"] = _recent_receipts(ctx)
    report["pending_materials"] = _pending_materials(ctx, report)
    report["source_checks"] = _source_checks(ctx, report["object"])
    if ctx.session.running:
        ctx.state["touched_turn"] = _turn_key(ctx)
    return Result(report, [{"type": "inquiry_report", **report}])


def _pending_materials(ctx, report):
    """New material requires relevance assessment, not automatic truth promotion."""
    node = report["object"]
    bound = {dep["ref"] for item in node.get("premises", []) for dep in item.get("dependencies", [])}
    bound.update(value["ref"] for item in node.get("assessments", [])
                 for receipt in item.get("receipts", []) for value in receipt.get("refs", []))
    baseline = ctx.records.meta(report["ref"]).get("material_baseline", 0)
    pending = []
    for receipt in _receipts(ctx)[baseline:]:
        if receipt.get("status") != "ok" or receipt.get("category") not in inquiry.INVESTIGATION_CATEGORIES:
            continue
        for value in receipt.get("refs", []):
            ref = value.get("ref")
            if not ref or ref in bound or ref in pending:
                continue
            try:
                obj = ctx.records.get_exact(ref, Record)
            except (ValueError, TypeError):
                continue
            if obj.source_type != "web_fetch" and any(field.origin in {"database", "extracted"} and field.value for key, field in obj.fields.items() if key in {"text", "full_text", "content"}):
                pending.append(ref)
    return pending


def list_objects(ctx, p: ListParams) -> Result:
    matches = []
    for ref, obj in reversed(ctx.records.all(Artifact)):
        if obj.kind != "inquiry" or ctx.records.current_ref(ref) != ref:
            continue
        node = inquiry.unpack(obj)
        if p.query.casefold() not in (node["question"] + " " + node.get("goal", "")).casefold():
            continue
        report = inquiry.inspect(ctx.records, ref, receipts=_receipts(ctx))
        matches.append({"ref": ref, "question": node["question"], "goal": node.get("goal", ""),
                        "readiness": report["readiness"], "blockers": report.get("blockers", []),
                        "next_steps": report.get("next_steps", []), "stale": report.get("stale", False)})
    items = matches[p.offset:p.offset + p.limit]
    data = {"items": items, "total": len(matches), "query": p.query, "offset": p.offset,
            "next_offset": p.offset + len(items) if p.offset + len(items) < len(matches) else None,
            "active_ref": ctx.session.state.get("inquiry", {}).get("active_ref"),
            "recent_receipts": _recent_receipts(ctx)}
    return Result(data, [{"type": "inquiry_directory", **data}])


def _lines(values):
    return ["- " + (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)) for item in values]


def export_action(ctx, payload) -> Result:
    report = inspect_object(ctx, InspectParams.model_validate(payload)).content
    node, ref = report["object"], report["ref"]
    lines = ["# " + node["question"], "", "Generated: " + report["inspected_at"],
             f"Exact snapshot: {ref}; current version: {report['current_ref']}",
             "Recorded-work readiness: " + report["readiness"],
             "Readiness is a check of recorded work and dependencies. It is not a guarantee of legal or factual correctness.",
             "", "## Goal", "", node.get("goal") or "Not specified", "", "## Scope and assumptions", ""]
    lines += [f"- {key}: {value if value else 'Unknown'}" for key, value in node.get("scope", {}).items()]
    lines += ["", "## Open checks", ""] + (_lines(report.get("blockers", [])) or ["No recorded blockers."])
    lines += ["", "## Next useful steps", ""] + (_lines(report.get("next_steps", [])) or ["No next step recorded."])
    lines += ["", "## Decisive premises", "",
              "These are recorded model assessments, not independent semantic verification."]
    for value in report.get("premises", []):
        lines += ["", f"### {value['id']}: {value['statement']}", "",
                  "Current state: " + value["effective_status"] + "; basis: " + value["basis"],
                  "If true: " + value["if_true"], "If false: " + value["if_false"],
                  "Next check: " + value["next_check"],
                  "Linked claims: " + ", ".join(value["affected_claim_refs"]),
                  "Evidence: " + ", ".join(value["evidence_refs"])]
    lines += ["", "Claims needing recheck: " + ", ".join(report["affected_claim_refs"]),
              "Saved answer needs refresh: " + str(report["answer_needs_refresh"]),
              "New material awaiting relevance assessment: " + ", ".join(report.get("pending_materials", []))]
    for task in node.get("tasks", []):
        lines += ["", "## " + task["id"] + ": " + task["question"], "",
                  "State: " + task["status"] + ("; critical" if task.get("critical") else ""),
                  task.get("why", ""), "Plan: " + task.get("plan", ""),
                  "Execution receipts: " + (", ".join(task.get("receipt_ids", [])) or "None recorded"),
                  "Findings (model interpretation): " + task.get("findings", ""),
                  "Next step: " + task.get("next_step", "")]
    claim_refs = list(node.get("candidate_refs", []))
    direct_refs = []
    for assessment in report.get("assessments", []):
        lines += ["", "## Argument check: " + assessment["claim_ref"], "",
                  "Current verdict (model-assessed): " + assessment.get("effective_verdict", assessment.get("verdict", "inconclusive")),
                  "Recorded verdict: " + assessment.get("verdict", "inconclusive"),
                  "Decisive premise IDs: " + ", ".join(assessment.get("premise_ids", [])),
                  assessment.get("rationale", ""), "", "Conditions:"] + _lines(assessment.get("conditions", []))
        lines += ["Counterevidence: " + (", ".join(assessment.get("counterevidence_refs", [])) or "None linked"),
                  "Response: " + assessment.get("response", "")]
        claim_refs += [assessment["claim_ref"], *assessment.get("counterevidence_refs", [])]
        direct_refs += assessment.get("counterevidence_refs", [])
    saved_answer = node.get("answer") or {}
    lines += ["", "## Saved answer", "", saved_answer.get("text", "No answer saved."), "", "Conditions:"] + _lines(saved_answer.get("conditions", []))
    lines += ["", "Known gaps:"] + _lines(saved_answer.get("gaps", []))
    if saved_answer.get("review"):
        review = saved_answer["review"]
        lines += ["", "## Final model review", "", "Status: " + review["status"],
                  "Scope: supplied question and source contexts only; not expert verification."]
        lines += _lines(review.get("corrections", []))
    claim_refs += saved_answer.get("claim_refs", [])
    if claim_refs:
        lines += ["", "---", "", research.render_snapshot(ctx.records, "Exact supporting research snapshots", list(dict.fromkeys(claim_refs)))]
    bindings = {binding["id"]: binding for item in [*node.get("tasks", []), *node.get("assessments", []), *node.get("premises", [])]
                for binding in item.get("receipts", [])}
    if bindings:
        lines += ["", "## Actual execution log", ""]
        for binding in bindings.values():
            lines += [f"- {binding['id']}: {binding['tool']} ({binding['status']}; {binding['category']}); "
                      + "outputs: " + (", ".join(value["ref"] for value in binding["refs"]) or "none")]
            direct_refs += [value["ref"] for value in binding["refs"]]
    for source_ref in dict.fromkeys(direct_refs):
        try:
            obj = ctx.records.get_exact(source_ref)
        except (TypeError, ValueError):
            lines += ["", "Unavailable exact investigation material: " + source_ref]
            continue
        if not isinstance(obj, Record):
            continue
        lines += ["", "## Exact investigation material: " + source_ref, "",
                  f"Work: {obj.provider}/{obj.record_id} ({obj.source_type})", "Fingerprint: " + ctx.records.fingerprint(source_ref)]
        field_name = next((name for name in ("full_text", "text", "content") if name in obj.fields), None)
        if field_name:
            value = obj.fields[field_name].value
            lines += ["", f"{field_name} (origin: {obj.fields[field_name].origin}):", "", value[:4000]]
            if len(value) > 4000:
                lines += [f"", f"Material preview truncated at 4000 of {len(value)} characters; open exact material {source_ref} for the complete stored text."]
    exported = {"ref": ref, "readiness": report["readiness"], "inspected_at": report["inspected_at"],
                "markdown": "\n".join(lines), "filename": ref + "-inquiry.md"}
    return Result({key: value for key, value in exported.items() if key != "markdown"}, [{"type": "inquiry_export", **exported}])


def context_provider(ctx) -> str:
    ref = ctx.session.state.get("inquiry", {}).get("active_ref")
    if not ref:
        sources = _source_checks(ctx)
        return json.dumps({"source_checks": sources[:6]}, ensure_ascii=False) if sources else ""
    try:
        report = inquiry.inspect(ctx.records, ctx.records.current_ref(ref), receipts=_receipts(ctx))
    except (ValueError, TypeError, KeyError):
        return "Active inquiry is unavailable; inspect saved materials before continuing."
    node = report["object"]
    def short(value, limit):
        value = str(value)
        return value if len(value) <= limit else value[:limit - 1] + "…"

    # Bound individual values, then remove secondary context if necessary.
    # Cutting an encoded JSON string can hide readiness behind a long question
    # and leaves an invalid structured summary.
    summary = {"source_checks": [{"url": short(row["url"], 250), "status": row["status"]} for row in _source_checks(ctx, node)[:4]],
               "active_inquiry": report["ref"], "readiness": report["readiness"], "stale": report["stale"],
               "answer_needs_refresh": report["answer_needs_refresh"],
               "affected_claim_refs": report["affected_claim_refs"][:8],
               "pending_materials": _pending_materials(ctx, report)[:6],
               "premises": [{key: short(value[key], 130) for key in ("id", "statement", "effective_status", "method", "next_check")}
                            for value in report["premises"][:3]],
               "blocker_count": len(report.get("blockers", [])),
               "blockers": [{key: short(value[key], 90) for key in ("code", "ref", "task_id", "field", "detail") if key in value}
                            for value in report.get("blockers", [])[:4]],
               "next_steps": [{key: short(value[key], 110) for key in ("task_id", "premise_id", "method", "next_step") if key in value}
                              for value in report.get("next_steps", [])[:2]],
               "question": short(node["question"], 350), "goal": short(node.get("goal", ""), 200),
               "scope": {key: short(value, 100) if value is not None else None for key, value in node.get("scope", {}).items()},
               "tasks": [{key: short(task[key], 90) if isinstance(task[key], str) else task[key]
                          for key in ("id", "question", "status", "critical") if key in task}
                         for task in node.get("tasks", [])[:3]]}
    while len(json.dumps(summary, ensure_ascii=False)) > 2200:
        if summary["tasks"]:
            summary["tasks"].pop()
        elif summary["next_steps"]:
            summary["next_steps"].pop()
        elif summary["scope"]:
            summary["scope"].pop(next(reversed(summary["scope"])))
        elif summary["blockers"]:
            summary["blockers"].pop()
        elif len(summary["premises"]) > 1:
            summary["premises"].pop()
        else:
            summary["question"] = short(summary["question"], 160)
            summary["goal"] = short(summary["goal"], 80)
            break
    return json.dumps(summary, ensure_ascii=False)


def final_response_checks(ctx, text) -> list[dict]:
    ref = ctx.session.state.get("inquiry", {}).get("active_ref")
    if not ref or ctx.session.state.get("inquiry", {}).get("touched_turn") != _turn_key(ctx):
        return []
    try:
        report = inquiry.inspect(ctx.records, ctx.records.current_ref(ref), receipts=_receipts(ctx))
    except (ValueError, TypeError, KeyError):
        return [{"type": "inquiry_final_check", "ref": ref, "readiness": "open",
                 "items": [{"code": "inquiry_unavailable", "message": "The active saved investigation could not be inspected. Its recorded checks cannot currently be confirmed."}],
                 "note": "The investigation snapshot is unavailable or invalid; inspect saved materials before relying on its saved answer."}]
    # A clarification or early investigation needs no alarming final-answer
    # badge. A saved answer can still acquire new dependency gaps later.
    if not report["object"].get("answer"):
        return []
    saved = report["object"]["answer"]["text"].strip()
    delivered = text.strip()
    digest = hashlib.sha256(saved.encode("utf-8")).hexdigest()
    if saved != delivered:
        return [{"type": "inquiry_final_check", "ref": report["ref"], "readiness": "open",
                 "answer_matches": False, "saved_answer_sha256": digest,
                 "delivered_answer_sha256": hashlib.sha256(delivered.encode("utf-8")).hexdigest(),
                 "items": [{"code": "answer_mismatch", "message": "The saved checks belong to a different answer. This displayed reply has not been checked against that saved version."}],
                 "note": "Inspect the saved answer and its evidence; its checks do not certify this reply."}]
    return [{"type": "inquiry_final_check", "ref": report["ref"], "readiness": report["readiness"],
             "answer_matches": True, "answer_sha256": digest,
             "items": (report.get("blockers") or report.get("issues") or [])[:6],
             "note": report["note"]}]


def prepare_completion(ctx, text):
    """One final completion attempt if real investigation bypassed saved checks.

    No extra planning loop, no retry cascade, and no duplicate review after
    conclude. Only sources actually returned in this user turn are included.
    """
    from plugins.inquiry.review import PROMPT, task_context

    runtime = ctx.session.runtime
    if runtime is None or not ctx.session.running:
        return None
    start = runtime.receipt_start
    if not text.strip():
        return None
    state = ctx.session.state.get("inquiry", {})
    if state.get("touched_turn") == _turn_key(ctx) and state.get("active_ref"):
        current = inquiry.inspect(ctx.records, ctx.records.current_ref(state["active_ref"]), receipts=_receipts(ctx))
        saved = current["object"].get("answer") or {}
        if saved.get("text", "").strip() == text.strip() and not current["answer_needs_refresh"]:
            return None
    rows = [row for row in _receipts(ctx)[start:] if row.get("category") in inquiry.INVESTIGATION_CATEGORIES
            and row.get("status") == "ok" and row.get("refs")]
    if not rows or len(text) < 120:
        return None
    sources = []
    refs = list(dict.fromkeys(value["ref"] for row in rows for value in row["refs"]))
    for ref in refs[-4:]:
        record = ctx.records.get_exact(ref)
        if not isinstance(record, Record):
            continue
        observations = [obs for row in rows for obs in row.get("observations", []) if obs.get("ref") == ref][-4:]
        if not observations:
            field = next((key for key in ("full_text", "text", "content") if key in record.fields), "")
            observations = [{"field": field, "offset": 0, "end": 5000}]
        for obs in observations:
            field = record.fields.get(obs["field"])
            if field is not None and field.origin != "model":
                offset = max(0, obs.get("offset", 0) - 250)
                value = field.value[offset:min(len(field.value), obs.get("end", offset + 3000) + 250)][:4000]
                sources.append({"source_ref": ref, "field": obs["field"], "offset": offset, "text": value})
    if not sources:
        return None
    sources = [{**item, "source_ref": item["ref"]} for item in _review_contexts(ctx,
               [{**source, "ref": source["source_ref"]} for source in sources])]
    pending_calculations = _pending_calculations(ctx)
    payload = {"task": task_context(ctx), "candidate_answer": text,
               "validated_calculations": pending_calculations,
               "calculation_specs": (ctx.state.get("pending_calculations") or {}).get("specs", []) if pending_calculations else [],
               "source_contexts": sources, "active_inquiry": state.get("active_ref") if state.get("touched_turn") == _turn_key(ctx) else None}
    messages = [{"role": "system", "content": PROMPT + "\nFor this final completion, call inquiry__conclude exactly once instead of returning JSON. "
                "Correct the candidate. Keep the answer under 1200 characters, ONE decisive check with 1-2 short exact source quotes, and at most one necessary unknown premise. Avoid a full essay or repeating tables. Copy SHORT CONTIGUOUS exact quotes from supplied contexts (no ellipses). "
                "and give a concise answer with conditions/gaps and actual paragraph/list newlines. Use observations for attributed exact quotes; calculations is required: supply quantitative comparisons (operation, before, after, exact user/source quote, optional source_ref), or [] when none. The server appends arithmetic; do not claim it proves causation. Classify literal source statements as direct. For inferences or hypotheses supply 1–3 concrete decisive premises inline (statement, if_true, if_false, next_check); untested assumptions stay unknown. "
                "Here the tool schema governs: premises contains complete inline premises, not the JSON review's append-only patch; retain existing IDs with premise_ids. "
                "Copy calculation_specs unchanged when supplied; they were already validated. User numbers belong in calculations with source_ref omitted, never in a methodological source record. Do not calculate a derived comparison using invented input quotes. Do not invent new sources or user facts. Existing checks may remain open."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    observed = []
    for source in sources:
        if len(observed) >= 2:
            break
        try:
            observed += evaluate_observations(ctx, [Observation(source_ref=source["source_ref"],
                field=source["field"], quote=source["text"][:500])])
        except ValueError:
            continue
    fallback, _ = _conditional_answer(text, [], {}, observations=observed, calculations=pending_calculations)
    return CompletionRequest(messages=messages, tool_name="inquiry__conclude", failure_reply=fallback)


PLUGIN = Plugin(
    name="inquiry", title="Question and investigation",
    description="Investigate legal or social questions. After obtaining decisive source passages, conclude saves evidence, key checks and the final answer in one call. Frame/pursue/assess are optional for longer work.",
    tools=[Tool("conclude", "Finish an investigation: answer + calculations (numeric inputs and exact user/source quote, or [] if none) + 1-2 checks (short exact passages, reasoning, objection, response). For unknown premises include both if_true and if_false. Saves and delivers directly; no prior frame needed.", ConcludeParams, conclude),
           Tool("frame", "Save a question, goal, context and useful subquestions; supply prior_ref to refine it after clarification while preserving previous investigation.", FrameParams, frame),
           Tool("premise", "Save or update one decisive premise: what changes if true/false, and the next concrete source check or user question. Actual source receipts/user quotes are bound automatically; status remains a model assessment. Use its stable ID when new material arrives.", PremiseParams, premise),
           Tool("pursue", "Update one subquestion with a plan, actual execution receipts, findings and the next useful step. Does not perform searches itself.", PursueParams, pursue),
           Tool("assess", "Check a saved research claim's inference, applicability and counterevidence; keep the explanation as a model assessment.", AssessParams, assess),
           Tool("inspect", "Read a saved or active inquiry, its open checks, next steps and recent actual execution receipts.", InspectParams, inspect_object),
           Tool("list", "Find saved questions and recent investigation execution receipts.", ListParams, list_objects)],
    actions={"list": lambda ctx, payload: list_objects(ctx, ListParams.model_validate(payload)),
             "inspect": lambda ctx, payload: inspect_object(ctx, InspectParams.model_validate(payload)),
             "export": export_action},
    readonly_actions=("list", "inspect", "export"), requires=("research",),
    ui=Path(__file__).parent, default_enabled=True, category="function",
    context_provider=context_provider, final_response_checks=final_response_checks,
    prepare_completion=prepare_completion,
)
