"""Versioned investigations of an open question, built from existing records.

This module records plans and semantic assessments as assertions.  Only the
server-owned tool receipt table establishes that a tool actually ran.  Neither
a successful receipt nor an intact citation proves that an answer is correct.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

from core import research
from core.schema_migration import upgrade
from core.tool_contracts import Artifact, Derivation, Field, Record

SCHEMA = "inquiry.v1"
# Older schema -> function returning the next schema's dict. Empty until a v2 exists.
MIGRATIONS: dict = {}
SCOPE_FIELDS = ("jurisdiction", "as_of", "procedure", "factual_context")
TASK_STATUSES = {"open", "investigating", "resolved", "blocked", "inconclusive"}
VERDICTS = {"supported", "limited", "disputed", "inconclusive"}
INVESTIGATION_CATEGORIES = {"source", "extract", "read"}
PREMISE_STATUSES = {"unknown", "supported", "refuted"}
CLAIM_KINDS = {"direct", "inference", "hypothesis"}


def _text(value, name, limit=8000, *, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} must be {'nonempty ' if required else ''}text of at most {limit} characters.")
    return value.strip()


def _list(value, name, limit=80):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise ValueError(f"{name} must be a list of at most {limit} items.")
    return list(value)


def _scope(value):
    if value is None:
        value = {}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, Mapping) or set(value) - set(SCOPE_FIELDS):
        raise ValueError("Scope accepts jurisdiction, as_of, procedure and factual_context only.")
    return {key: _text(value[key], key, 2000) or None if value.get(key) is not None else None
            for key in SCOPE_FIELDS}


def _strings(values, name, *, limit=40):
    return list(dict.fromkeys(_text(value, name, 2000, required=True)
                              for value in _list(values, name, limit)))


def _claim(store, ref):
    node = research.unpack(store.get_exact(ref, Artifact))
    if node["type"] not in {"claim", "premise", "conclusion"}:
        raise ValueError("A candidate explanation must reference an exact saved claim, premise or conclusion.")
    return node


def _refs(store, values, name, *, claims=False):
    refs = _strings(values, name, limit=80)
    for ref in refs:
        _claim(store, ref) if claims else store.get_exact(ref)
    return refs


def unpack(artifact):
    if not isinstance(artifact, Artifact) or artifact.kind != "inquiry":
        raise ValueError("Expected a saved inquiry object.")
    return upgrade(json.loads(artifact.content), SCHEMA, MIGRATIONS, "inquiry")


def pack(data):
    """A compact model-origin snapshot, never a newly grounded source fact."""
    data = {**data, "schema": SCHEMA, "legal_verification": False}
    content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return Artifact("inquiry", content, Derivation((Field(content, "model"),), "inquiry.snapshot.v1"))


def _prior(store, ref):
    data = unpack(store.get_exact(ref, Artifact))
    current = store.current_ref(ref)
    if current != ref:
        raise ValueError(f"Inquiry revision conflict: {ref} was revised to {current}. Read the current version first.")
    return copy.deepcopy(data)


def _receipt_table(receipts):
    if receipts is None:
        return {}
    if isinstance(receipts, Mapping):
        entries = receipts.values()
    elif isinstance(receipts, (list, tuple)):
        entries = receipts
    else:
        raise ValueError("Tool receipts must come from the server-owned receipt table.")
    return {item["id"]: item for item in entries
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)}


def _receipt_snapshot(store, item):
    """Bind a real receipt to its exact outputs; never follow replacement refs."""
    status = item.get("status")
    if status not in {"ok", "no_match", "error"}:
        raise ValueError("Unsupported tool receipt status.")
    refs = []
    for value in item.get("refs", []):
        if isinstance(value, str):
            value = {"ref": value, "fingerprint": store.fingerprint(value)}
        if not isinstance(value, Mapping) or not isinstance(value.get("ref"), str):
            raise ValueError("Tool receipt has an invalid output binding.")
        ref = value["ref"]
        if value.get("fingerprint") != store.fingerprint(ref):
            raise ValueError(f"Tool receipt output fingerprint does not match {ref}.")
        refs.append({"ref": ref, "fingerprint": value["fingerprint"]})
    snapshot = {key: copy.deepcopy(item.get(key))
                for key in ("id", "tool", "category", "status", "error_kind", "coverage", "sequence", "generation", "observations")}
    snapshot["refs"] = refs
    if "executed" in item:
        snapshot["executed"] = item["executed"]
    return snapshot


def _receipt_bindings(store, ids, receipts):
    table = _receipt_table(receipts)
    result = []
    for identifier in _strings(ids, "receipt_ids", limit=80):
        if identifier not in table:
            raise ValueError(f"Unknown trusted tool receipt: {identifier}. Use actual completed tool receipt IDs.")
        result.append(_receipt_snapshot(store, table[identifier]))
    return result


def _receipt_hash(receipt):
    content = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _source_binding(store, ref, bindings):
    """Accept actual acquisition or a valid original-text read of a Record.

    This establishes provenance and execution, never that the material proves
    the premise. A generated research object cannot become a source here.
    """
    record = store.get_exact(ref, Record)
    originals = {name: field for name, field in record.fields.items()
                 if field.origin in {"database", "extracted"} and field.value.strip()}
    if not originals:
        return False
    for binding in bindings:
        if binding.get("status") != "ok" or binding.get("executed") is False:
            continue
        if not any(value["ref"] == ref for value in binding.get("refs", [])):
            continue
        if binding.get("category") in {"source", "extract"}:
            return True
        if binding.get("category") != "read":
            continue
        for observation in binding.get("observations") or []:
            field = originals.get(observation.get("field"))
            start, end = observation.get("offset"), observation.get("end")
            if (observation.get("ref") != ref or field is None
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(field.value)):
                continue
            if (observation.get("origin") == field.origin
                    and observation.get("text_sha256") == research.digest(field.value[start:end])):
                return True
    return False


def _premise_fingerprint(value):
    # Updating the next search step alone does not change an argument's basis.
    basis = {key: value.get(key) for key in (
        "id", "statement", "if_true", "if_false", "method", "status",
        "evidence_refs", "receipts", "dependencies", "user_quote", "user_binding")}
    return _receipt_hash(basis)


def premise(store, prior, *, id="", statement, if_true, if_false, next_check,
            method="source", status="unknown", evidence_refs=None,
            receipt_ids=None, receipts=None, user_quote="", user_texts=None):
    """Record a conclusion-changing unknown and the next way to investigate it.

    Resolved states are still model assessments. Source execution and exact
    user wording are separate forms of support, and neither certifies truth.
    Replacing this entry does not silently reassess claims that relied on it.
    """
    data = _prior(store, prior)
    items = data.setdefault("premises", [])
    identifier = _text(id, "premise id", 100)
    if not identifier:
        number = 1
        while any(value["id"] == f"p{number}" for value in items):
            number += 1
        identifier = f"p{number}"
    if method not in {"source", "user"} or status not in PREMISE_STATUSES:
        raise ValueError("A premise requires method source/user and status unknown/supported/refuted.")
    refs = _refs(store, evidence_refs, "evidence_refs")
    for ref in refs:
        record = store.get_exact(ref, Record)
        if not any(field.origin in {"database", "extracted"} and field.value.strip()
                   for field in record.fields.values()):
            raise ValueError("Premise evidence must contain original source text, not model-written fields.")
    bindings = _receipt_bindings(store, receipt_ids, receipts)
    # executed was added after the original v1 receipt snapshot format. Keep
    # older snapshots compatible, but never accept a known unexecuted call.
    table = _receipt_table(receipts)
    if any(table[item["id"]].get("executed") is False for item in bindings):
        raise ValueError("Premise evidence requires an actually executed source or read receipt.")
    for ref in refs:
        if not _source_binding(store, ref, bindings):
            raise ValueError(f"Premise evidence {ref} requires a successful source/extract or original-text read receipt for that exact Record.")
    quote = _text(user_quote, "user_quote", 8000)
    user_binding = None
    if method == "user" and quote:
        if user_texts is not None and not isinstance(user_texts, (list, tuple)):
            raise ValueError("user_texts must be the server-owned list of user message texts.")
        matches = [(index, text, text.find(quote)) for index, text in enumerate(user_texts or [])
                   if isinstance(text, str) and quote in text]
        if not matches:
            raise ValueError("user_quote must be an exact passage from the server-owned user messages.")
        index, text, start = matches[-1]
        user_binding = {"message_index": index, "text_sha256": research.digest(text),
                        "start": start, "end": start + len(quote), "quote_sha256": research.digest(quote)}
    elif quote:
        raise ValueError("user_quote requires method=user; user reports are not external source verification.")
    if status != "unknown":
        if method == "source" and not refs:
            raise ValueError("A supported/refuted source premise requires evidence_refs and trusted receipts.")
        if method == "user" and user_binding is None:
            raise ValueError("A supported/refuted user premise requires an exact user_quote from server-owned messages.")
    value = {"id": identifier, "statement": _text(statement, "statement", 4000, required=True),
             "if_true": _text(if_true, "if_true", 4000, required=True),
             "if_false": _text(if_false, "if_false", 4000, required=True),
             "next_check": _text(next_check, "next_check", 4000, required=True),
             "method": method, "status": status, "evidence_refs": refs,
             "receipt_ids": [item["id"] for item in bindings], "receipts": bindings,
             "dependencies": [research.dependency(store, ref, "premise_evidence") for ref in refs],
             "user_quote": quote, "user_binding": user_binding,
             "basis": "user_reported" if method == "user" and user_binding else "source_bound" if refs else "unestablished",
             "assessment": "model_assessed", "semantic_verification": False, "legal_verification": False}
    if len(items) >= 40 and not any(item["id"] == identifier for item in items):
        raise ValueError("An inquiry supports at most 40 decisive premises; keep the investigation focused.")
    data["premises"] = [value if item["id"] == identifier else item for item in items]
    if not any(item["id"] == identifier for item in items):
        data["premises"].append(value)
    return _finish(store, data, prior)


def _task_current_receipts(task):
    """The latest evidenced attempt remains current through plan-only edits.

    The full receipt list is an audit log. New executed attempts replace the
    current binding; an empty plan cannot erase an earlier source problem.
    Older snapshots without attempt history conservatively retain their log.
    """
    bindings = task.get("receipts", [])
    if "current_receipt_ids" in task:
        identifiers = set(task["current_receipt_ids"])
        return [item for item in bindings if item["id"] in identifiers]
    for attempt in reversed(task.get("attempts", [])):
        identifiers = set(attempt.get("receipt_ids", []))
        if identifiers:
            return [item for item in bindings if item["id"] in identifiers]
    return bindings


def _dependency_refs(data):
    refs = [(ref, "candidate") for ref in data["candidate_refs"]]
    for assessment in data["assessments"]:
        refs.extend((ref, "counterevidence") for ref in assessment["counterevidence_refs"])
    for task in data["tasks"]:
        for receipt in _task_current_receipts(task):
            refs.extend((value["ref"], "investigation_output") for value in receipt["refs"])
    for assessment in data["assessments"]:
        for receipt in assessment.get("receipts", []):
            refs.extend((value["ref"], "assessment_output") for value in receipt["refs"])
    for item in data.get("premises", []):
        refs.extend((value["ref"], "premise_evidence") for value in item.get("dependencies", []))
    for assessment in data["assessments"]:
        for snapshot in assessment.get("premise_snapshots", []):
            refs.extend((value["ref"], "assessed_premise_evidence") for value in snapshot.get("dependencies", []))
    refs.extend((ref, "answer_source") for ref in _answer_source_refs(data.get("answer")))
    return refs


def _answer_source_refs(answer):
    """Sources of server-bound observations/calculations, not prose URL claims."""
    delivery = (answer or {}).get("delivery_evidence") or {}
    refs = []
    for key in ("observations", "calculations"):
        for value in delivery.get(key, []):
            provenance = value.get("provenance") or {}
            ref = provenance.get("source_ref")
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def _dependencies(store, data):
    unique = {}
    for ref, role in _dependency_refs(data):
        unique.setdefault(ref, research.dependency(store, ref, role))
    return list(unique.values())


def _finish(store, data, prior=None):
    data["dependencies"] = _dependencies(store, data)
    if prior is not None:
        # Recheck immediately before handing the revision to its caller.
        if store.current_ref(prior) != prior:
            raise ValueError("Inquiry revision conflict. Read the current version first.")
        research.validate_revision(store, prior, data["dependencies"])
        data["revision_of"] = prior
    return pack(data)


def frame(store, question, goal="", scope=None, questions=None, candidate_refs=None):
    """Organize an open question. Empty scope values remain explicit unknowns."""
    question = _text(question, "question", 12000, required=True)
    tasks, seen = [], set()
    questions = _list(questions, "questions", 40)
    if not questions:
        questions = [{"question": question, "critical": True,
                      "why": "Resolve the user's main question using relevant evidence."}]
    for index, item in enumerate(questions, 1):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, Mapping):
            raise ValueError("Each investigation question must be text or an object.")
        identifier = _text(item.get("id") or f"q{index}", "task id", 100, required=True)
        if identifier in seen:
            raise ValueError("Investigation task IDs must be unique.")
        seen.add(identifier)
        critical = item.get("critical", True)
        if type(critical) is not bool:
            raise ValueError("critical must be a boolean.")
        tasks.append({"id": identifier, "question": _text(item.get("question", ""), "task question", 4000, required=True),
                      "critical": critical, "why": _text(item.get("why", ""), "why", 2000), "status": "open",
                      "plan": "", "receipt_ids": [], "receipts": [], "current_receipt_ids": [],
                      "findings": "", "next_step": "", "attempts": []})
    return _finish(store, {"schema": SCHEMA, "question": question, "goal": _text(goal, "goal", 4000),
                           "scope": _scope(scope), "tasks": tasks,
                           "candidate_refs": _refs(store, candidate_refs, "candidate_refs", claims=True),
                           "assessments": [], "premises": [], "answer": None})


def refine(store, prior, scope=None, goal=None, questions=None, candidate_refs=None, question=None):
    """Continue after clarification without discarding prior investigation.

    Scope is a patch: omitted fields are retained and explicit null becomes
    unknown. Questions append by ID; changing a question resets its current
    completion assertion while retaining the actual prior attempt log.
    """
    data = _prior(store, prior)
    if question is not None:
        data["question"] = _text(question, "question", 12000, required=True)
    if scope is not None:
        if hasattr(scope, "model_dump"):
            scope = scope.model_dump(exclude_unset=True)
        normalized = _scope(scope)
        data["scope"].update({key: normalized[key] for key in scope})
    if goal is not None:
        data["goal"] = _text(goal, "goal", 4000)
    if candidate_refs is not None:
        data["candidate_refs"] = _refs(store, candidate_refs, "candidate_refs", claims=True)
        data["assessments"] = [value for value in data["assessments"] if value["claim_ref"] in data["candidate_refs"]]
    for value in _list(questions, "questions", 40):
        if hasattr(value, "model_dump"):
            value = value.model_dump(exclude_none=True)
        if isinstance(value, str):
            value = {"question": value}
        if not isinstance(value, Mapping):
            raise ValueError("Each investigation question must be text or an object.")
        identifier = value.get("id")
        if not identifier:
            number = len(data["tasks"]) + 1
            while any(item["id"] == f"q{number}" for item in data["tasks"]):
                number += 1
            identifier = f"q{number}"
        existing = next((item for item in data["tasks"] if item["id"] == identifier), None)
        # Reuse frame's normalization; refs and receipt provenance are absent
        # from model-supplied questions, so no execution can be injected here.
        normalized = unpack(frame(store, data["question"], questions=[{**value, "id": identifier}]))["tasks"][0]
        if existing is None:
            if len(data["tasks"]) >= 80:
                raise ValueError("An inquiry supports at most 80 subquestions; split it into a narrower investigation.")
            data["tasks"].append(normalized)
        else:
            changed = existing["question"] != normalized["question"]
            existing.update({key: normalized[key] for key in ("question", "critical", "why")})
            if changed:
                existing.update(status="open", plan="", findings="", next_step="")
                existing["attempts"] = (existing.get("attempts", []) + [{"status": "open", "plan": "Question revised; prior executions remain historical.",
                                                                       "findings": "", "next_step": "", "receipt_ids": []}])[-40:]
    if data.get("answer"):
        data["answer"]["needs_refresh"] = True
    return _finish(store, data, prior)


def pursue(store, prior, task_id, status="investigating", receipt_ids=None, findings="", next_step="", receipts=None, plan=""):
    """Record a plan/attempt. Declaring completion cannot invent execution."""
    data = _prior(store, prior)
    if status not in TASK_STATUSES:
        raise ValueError("Unknown investigation task status.")
    task = next((item for item in data["tasks"] if item["id"] == task_id), None)
    if task is None:
        raise ValueError("No such investigation task in this inquiry.")
    bindings = _receipt_bindings(store, receipt_ids, receipts)
    if status == "resolved" and not bindings:
        raise ValueError("A resolved task requires trusted tool receipts; a plan or model assertion is not execution.")
    fields = {"status": status, "plan": _text(plan, "plan", 4000),
              "findings": _text(findings, "findings"), "next_step": _text(next_step, "next_step", 4000)}
    history = {item["id"]: item for item in task.get("receipts", [])}
    history.update({item["id"]: item for item in bindings})
    if len(history) > 160:
        raise ValueError("This task has too many receipts; split the investigation into another inquiry.")
    current_ids = ([item["id"] for item in bindings] if bindings
                   else [item["id"] for item in _task_current_receipts(task)])
    task.update(fields, receipt_ids=list(history), receipts=list(history.values()), current_receipt_ids=current_ids)
    task["scope_snapshot"] = copy.deepcopy(data["scope"])
    task["question_snapshot"] = data["question"]
    task["attempts"] = (task.get("attempts", []) + [{**fields, "receipt_ids": [item["id"] for item in bindings]}])[-40:]
    # A revision changes the investigation underpinning a previously written
    # answer. Keep its text, but inspection must request a fresh synthesis.
    if data.get("answer"):
        data["answer"]["needs_refresh"] = True
    return _finish(store, data, prior)


def _direct_basis(store, claim_ref):
    """An original quote is mechanical; a paraphrase is still interpretation.

    Equality identifies what the source says, never whether it is true or
    applies. Do not trust a model's ``direct`` label or a stored basis label.
    """
    claim = _claim(store, claim_ref)
    excerpts = []
    for link in claim.get("evidence", []):
        if link.get("relation") != "supports":
            continue
        try:
            excerpt = research.unpack(store.get_exact(link["excerpt_ref"], Artifact))
            if excerpt["type"] != "excerpt" or excerpt["quote"] != claim["text"]:
                continue
            source = excerpt["source"]
            record = store.get_exact(source["ref"], Record)
            field = record.fields.get(source["field"])
            start, end = source["start"], source["end"]
            if (field is not None and field.origin in {"database", "extracted", "user"}
                    and type(start) is int and type(end) is int and 0 <= start < end <= len(field.value)
                    and store.fingerprint(source["ref"]) == source["fingerprint"]
                    and research.digest(field.value) == source["field_sha256"]
                    and field.value[start:end] == excerpt["quote"]
                    and research.digest(excerpt["quote"]) == source["quote_sha256"]):
                excerpts.append(link["excerpt_ref"])
        except (KeyError, TypeError, ValueError):
            continue
    return {"direct_basis": "source_quote" if excerpts else "model_interpretation",
            "direct_excerpt_refs": list(dict.fromkeys(excerpts))}


def assess(store, prior, claim_ref, rationale, conditions=None, counterevidence_refs=None,
           response="", receipt_ids=None, receipts=None, scope=None, verdict="inconclusive", critical=True,
           premise_ids=None, claim_kind="inference"):
    """Preserve a semantic assessment separately from mechanical evidence checks."""
    data = _prior(store, prior)
    _claim(store, claim_ref)
    if verdict not in VERDICTS or type(critical) is not bool:
        raise ValueError("Assessment requires a known verdict and boolean critical flag.")
    if claim_kind not in CLAIM_KINDS:
        raise ValueError("claim_kind must be direct, inference or hypothesis.")
    ids = _strings(premise_ids, "premise_ids")
    premises = {item["id"]: item for item in data.get("premises", [])}
    if any(identifier not in premises for identifier in ids):
        raise ValueError("Unknown premise ID; save the decisive premise before binding it to an assessment.")
    assessment = {"claim_ref": claim_ref, "verdict": verdict, "critical": critical,
                  "rationale": _text(rationale, "rationale", 8000, required=True),
                  "scope": _scope(scope), "conditions": _strings(conditions, "conditions"),
                  "counterevidence_refs": _refs(store, counterevidence_refs, "counterevidence_refs"),
                  "response": _text(response, "counterevidence response"),
                  "receipts": _receipt_bindings(store, receipt_ids, receipts),
                  "claim_kind": claim_kind, "premise_ids": ids,
                  "premises_declared": premise_ids is not None,
                  "premise_snapshots": [{"id": identifier, "fingerprint": _premise_fingerprint(premises[identifier]),
                                         "dependencies": copy.deepcopy(premises[identifier].get("dependencies", []))}
                                        for identifier in ids],
                  "assessment": "model_assessed", "semantic_verification": False, "legal_verification": False}
    if claim_kind == "direct":
        assessment.update(_direct_basis(store, claim_ref))
    data["assessments"] = [item for item in data["assessments"] if item["claim_ref"] != claim_ref] + [assessment]
    if claim_ref not in data["candidate_refs"]:
        data["candidate_refs"].append(claim_ref)
    if data.get("answer") and claim_ref in data["answer"].get("claim_refs", []):
        data["answer"]["needs_refresh"] = True
    return _finish(store, data, prior)


def answer(store, prior, text, claim_refs=None, conditions=None, receipts=None, gaps=None, review=None,
           delivery_evidence=None):
    """Save a synthesis; its current readiness is always computed at inspection."""
    data = _prior(store, prior)
    claims = _refs(store, claim_refs, "claim_refs", claims=True)
    for ref in claims:
        if ref not in data["candidate_refs"]:
            data["candidate_refs"].append(ref)
    data["answer"] = {"text": _text(text, "answer", 24000, required=True), "claim_refs": claims,
                      "conditions": _strings(conditions, "conditions"), "gaps": _strings(gaps, "gaps"),
                      "needs_refresh": False, "legal_verification": False}
    if review is not None:
        data["answer"]["review"] = copy.deepcopy(review)
    if delivery_evidence is not None:
        if not isinstance(delivery_evidence, dict):
            raise ValueError("Delivery evidence must be a server-validated object.")
        data["answer"]["delivery_evidence"] = copy.deepcopy(delivery_evidence)
        for ref in _answer_source_refs(data["answer"]):
            store.get_exact(ref, Record)
    return _finish(store, data, prior)


def _coverage_partial(coverage):
    if not coverage:
        return False
    if isinstance(coverage, str):
        return coverage.lower() in {"partial", "incomplete", "stale", "unknown"}
    if isinstance(coverage, Mapping):
        return (coverage.get("complete") is False or coverage.get("partial") is True
                or coverage.get("status") in {"partial", "incomplete", "stale", "unknown"}
                or coverage.get("stale") is True)
    return False


def inspect(store, ref, receipts=None):
    """Report remaining work without certifying completeness or legal meaning."""
    data = unpack(store.get_exact(ref, Artifact))
    table, issues, tasks, assessments, reports = _receipt_table(receipts), [], [], [], []
    premise_reports, affected_claims = [], []

    def issue(code, *, blocking=False, **detail):
        value = {"code": code, "blocking": blocking, **detail}
        if value not in issues:
            issues.append(value)

    current = store.current_ref(ref)
    if current != ref:
        issue("inquiry_revised", blocking=True, ref=ref, current_ref=current)
    for key in SCOPE_FIELDS:
        if not data["scope"].get(key):
            issue("scope_unknown", field=key, detail="Clarify applicability, or explicitly record not_applicable with a reason.")
    stale = current != ref
    # Older inquiry snapshots included every historical attempt's outputs in
    # this list. Inspect only currently relied-on refs, keeping the recorded
    # fingerprints rather than recalculating them from possibly changed data.
    active_refs = {target for target, _ in _dependency_refs(data)}
    answer_sources = set(_answer_source_refs(data.get("answer")))
    changed_answer_sources = []
    dependencies = [value for value in data.get("dependencies", []) if value["ref"] in active_refs]
    for dependency in dependencies:
        target = dependency["ref"]
        try:
            if store.fingerprint(target) != dependency["fingerprint"]:
                issue("dependency_changed", blocking=True, ref=target)
                stale = True
                if target in answer_sources:
                    changed_answer_sources.append(target)
            latest = store.current_ref(target)
            if latest != target:
                issue("dependency_revised", blocking=True, ref=target, current_ref=latest)
                stale = True
                if target in answer_sources:
                    changed_answer_sources.append(target)
        except (TypeError, ValueError):
            issue("dependency_unavailable", blocking=True, ref=target)
            stale = True
            if target in answer_sources:
                changed_answer_sources.append(target)

    def check_receipts(bindings, owner, *, historical=False):
        checked = []
        for binding in bindings:
            identifier = binding["id"]
            try:
                actual = _receipt_snapshot(store, table[identifier])
                if "observations" not in binding:
                    # Early v1 snapshots predate observation capture. Retain
                    # their narrower binding without inventing a prior read.
                    actual.pop("observations", None)
                if "executed" not in binding:
                    actual.pop("executed", None)
                valid = _receipt_hash(actual) == _receipt_hash(binding)
            except (KeyError, ValueError, TypeError):
                valid = False
            if not valid:
                issue("historical_receipt_unavailable_or_changed" if historical else "receipt_unavailable_or_changed",
                      blocking=not historical, receipt_id=identifier, **owner)
                continue
            checked.append(actual)
        return checked

    # Premise states are inspected once, then only explicitly linked claims
    # inherit their changes. Merely receiving another document is not a global
    # instruction to throw away every conclusion in the investigation.
    for value in data.get("premises", []):
        identifier = value["id"]
        owners = [row["claim_ref"] for row in data["assessments"]
                  if identifier in row.get("premise_ids", [])]
        reasons = []
        for dependency in value.get("dependencies", []):
            try:
                if store.fingerprint(dependency["ref"]) != dependency["fingerprint"]:
                    reasons.append("premise_source_changed")
                if store.current_ref(dependency["ref"]) != dependency["ref"]:
                    reasons.append("premise_source_revised")
            except (ValueError, TypeError):
                reasons.append("premise_source_unavailable")
        trusted = check_receipts(value.get("receipts", []), {"premise_id": identifier})
        if len(trusted) != len(value.get("receipts", [])):
            reasons.append("premise_receipt_unavailable_or_changed")
        if any(table.get(item["id"], {}).get("executed") is False for item in trusted):
            reasons.append("premise_source_not_executed")
        if value.get("method") == "source" and value.get("status") != "unknown":
            try:
                backed = bool(value.get("evidence_refs")) and all(
                    _source_binding(store, source_ref, trusted) for source_ref in value["evidence_refs"])
            except (ValueError, TypeError):
                backed = False
            if not backed:
                reasons.append("premise_source_not_checked")
        if value.get("method") == "user" and value.get("status") != "unknown":
            binding = value.get("user_binding") or {}
            if (not value.get("user_quote")
                    or binding.get("quote_sha256") != research.digest(value["user_quote"])):
                reasons.append("premise_user_binding_invalid")
        reasons = list(dict.fromkeys(reasons))
        effective_status = "unknown" if reasons else value["status"]
        if reasons:
            stale = True
            issue("premise_needs_recheck", blocking=bool(owners), premise_id=identifier,
                  claim_refs=owners, reasons=reasons)
        if effective_status == "unknown":
            issue("premise_unknown", blocking=bool(owners), premise_id=identifier,
                  claim_refs=owners, detail=value["statement"])
        elif effective_status == "refuted" and owners:
            issue("premise_refuted", blocking=True, premise_id=identifier, claim_refs=owners,
                  detail="This recorded premise is assessed as refuted; revise the reasoning that depends on it.")
        premise_reports.append({**value, "affected_claim_refs": owners,
                                "effective_status": effective_status, "needs_recheck": bool(reasons),
                                "recheck_reasons": reasons, "reason": "; ".join(reasons),
                                "trusted_receipt_count": len(trusted)})
    premise_by_id = {value["id"]: value for value in premise_reports}

    for task in data["tasks"]:
        current_bindings = _task_current_receipts(task)
        current_ids = {item["id"] for item in current_bindings}
        historical_bindings = [item for item in task.get("receipts", []) if item["id"] not in current_ids]
        trusted = check_receipts(current_bindings, {"task_id": task["id"]})
        historical = check_receipts(historical_bindings, {"task_id": task["id"]}, historical=True)
        active_ids = set(task["attempts"][-1]["receipt_ids"]) if task.get("attempts") else set()
        external = [item for item in trusted if item["category"] in INVESTIGATION_CATEGORIES]
        successful = [item for item in external if item["status"] == "ok" and item["refs"]]
        no_match = [item for item in external if item["status"] == "no_match"]
        errors = [item for item in external if item["status"] == "error"]
        critical = task["critical"]
        if task["status"] != "resolved":
            issue("critical_task_open" if critical else "task_open", blocking=critical,
                  task_id=task["id"], status=task["status"], question=task["question"])
        elif task.get("scope_snapshot") != data["scope"]:
            issue("task_scope_changed", blocking=critical, task_id=task["id"],
                  detail="The applicable context changed after the recorded attempt; review its relevance before completing the task again.")
        if task["status"] == "resolved" and task.get("question_snapshot") != data["question"]:
            issue("task_question_context_changed", blocking=critical, task_id=task["id"],
                  detail="The main question changed; confirm that the previous investigation still addresses it.")
        if errors:
            issue("source_execution_failed", blocking=critical, task_id=task["id"],
                  receipt_ids=[item["id"] for item in errors])
        if not successful:
            if no_match:
                issue("search_no_match", blocking=critical, task_id=task["id"],
                      detail="The search ran and returned no matches; this does not establish that no relevant authority or evidence exists.")
            if task["status"] == "resolved" and not errors and not no_match:
                issue("investigation_not_executed", blocking=critical, task_id=task["id"])
        elif not errors and any(item["status"] == "error" and item["category"] in INVESTIGATION_CATEGORIES for item in historical):
            issue("source_failure_recovered", task_id=task["id"],
                  detail="An earlier failed attempt is retained; later material was retrieved. Query adequacy remains asserted.")
        if any(_coverage_partial(item.get("coverage")) for item in successful):
            issue("source_coverage_partial", task_id=task["id"])
        latest = [item for item in external if item["id"] in active_ids]
        latest_success = any(item["status"] == "ok" and item["refs"] for item in latest)
        latest_errors = any(item["status"] == "error" for item in latest)
        latest_empty = any(item["status"] == "no_match" for item in latest)
        tasks.append({**task, "trusted_receipt_count": len(trusted) + len(historical),
                      "current_receipt_ids": [item["id"] for item in current_bindings],
                      "current_trusted_receipt_count": len(trusted),
                      "historical_receipt_count": len(historical_bindings),
                      "execution": "partial_failure" if latest_errors and latest_success
                      else "material_returned" if latest_success else "failed" if latest_errors else "no_match" if latest_empty else "not_executed",
                      "relevance_verification": "asserted_only", "legal_verification": False})

    by_ref = {item["claim_ref"]: item for item in data["assessments"]}
    if not data["candidate_refs"]:
        issue("candidate_explanations_missing", blocking=True)
    for candidate in data["candidate_refs"]:
        assessment = by_ref.get(candidate)
        critical = assessment.get("critical", True) if assessment else True
        try:
            report = research.inspect(store, candidate)
        except (ValueError, TypeError):
            issue("claim_unavailable", blocking=critical, ref=candidate)
            continue
        reports.append(report)
        if report["stale"]:
            stale = True
            issue("claim_stale", blocking=critical, ref=candidate, issues=report["issues"])
        for value in report["issues"]:
            code = value["code"]
            if code in {"unresolved_unknown", "unresolved_disputed", "support_gap", "inference_gap"}:
                issue(code, blocking=critical, ref=value.get("ref", candidate), claim_ref=candidate,
                      path=value.get("path", []))
            elif code == "unverified_assumption":
                issue("unverified_assumption", ref=value.get("ref", candidate), claim_ref=candidate)
            elif code == "opposing_evidence":
                issue("opposing_evidence_present", ref=value.get("ref", candidate), claim_ref=candidate,
                      detail="A recorded response does not remove the opposing evidence.")
            elif code == "scope_not_applicable":
                issue("scope_not_applicable", blocking=critical, ref=value.get("ref", candidate), claim_ref=candidate)
        if assessment is None:
            issue("key_claim_unassessed", blocking=critical, ref=candidate)
            continue
        recheck_reasons = ["claim_stale"] if report["stale"] else []
        snapshots = {value["id"]: value for value in assessment.get("premise_snapshots", [])}
        linked_premises = []
        for identifier in assessment.get("premise_ids", []):
            value = premise_by_id.get(identifier)
            snapshot = snapshots.get(identifier)
            if value is None:
                recheck_reasons.append("premise_missing")
                continue
            linked_premises.append(value)
            if snapshot is None or snapshot.get("fingerprint") != _premise_fingerprint(value):
                recheck_reasons.append("premise_changed")
            recheck_reasons.extend(value["recheck_reasons"])
        recheck_reasons = list(dict.fromkeys(recheck_reasons))
        needs_recheck = bool(recheck_reasons)
        if needs_recheck:
            affected_claims.append(candidate)
            issue("claim_needs_recheck", blocking=critical, claim_ref=candidate, reasons=recheck_reasons,
                  premise_ids=assessment.get("premise_ids", []))
        uncertain_premises = [value["id"] for value in linked_premises if value["effective_status"] != "supported"]
        if uncertain_premises:
            issue("claim_premises_unresolved", blocking=critical, claim_ref=candidate, premise_ids=uncertain_premises)
        kind = assessment.get("claim_kind", "inference")
        direct_basis = _direct_basis(store, candidate) if kind == "direct" else {}
        effective_verdict = assessment["verdict"]
        interpreted_direct = direct_basis.get("direct_basis") == "model_interpretation"
        if effective_verdict == "supported" and (needs_recheck or uncertain_premises or kind == "hypothesis" or interpreted_direct):
            effective_verdict = "limited"
        if interpreted_direct:
            issue("direct_claim_interpretation", ref=candidate,
                  detail="This wording is a model interpretation, not an exact supported source quote. Its meaning remains unverified.")
        if kind == "hypothesis":
            issue("hypothesis_not_established", ref=candidate,
                  detail="This is a possible explanation, not an established conclusion.")
        trusted = check_receipts(assessment.get("receipts", []), {"claim_ref": candidate})
        failed_checks = [item for item in trusted if item["category"] in INVESTIGATION_CATEGORIES and item["status"] == "error"]
        if failed_checks:
            issue("assessment_source_failed", blocking=critical, ref=candidate,
                  receipt_ids=[item["id"] for item in failed_checks])
        check_executed = any(item["category"] in INVESTIGATION_CATEGORIES
                             and item["status"] in {"ok", "no_match"} for item in trusted)
        if not check_executed:
            issue("countercheck_not_executed", blocking=critical, ref=candidate)
        if assessment["verdict"] in {"disputed", "inconclusive"}:
            issue("key_claim_" + assessment["verdict"], blocking=critical, ref=candidate)
        nodes = report.get("assertions") or [{"ref": candidate, **report["object"]}]
        for node in nodes:
            claim_scope = node.get("scope", {})
            if claim_scope.get("applicability", "unknown") == "unknown":
                issue("applicability_unknown", ref=node.get("ref", candidate))
            elif claim_scope.get("applicability") == "conditional":
                issue("applicability_conditional", ref=node.get("ref", candidate), detail=claim_scope.get("note", ""))
            for limitation in node.get("limitations", []):
                issue("claim_limitation", ref=node.get("ref", candidate), detail=limitation)
            for key in SCOPE_FIELDS:
                expected = data["scope"].get(key)
                observed = claim_scope.get(key) or assessment["scope"].get(key)
                if expected and observed and expected.casefold() != observed.casefold():
                    issue("scope_mismatch", blocking=critical, ref=node.get("ref", candidate), field=key,
                          inquiry_value=expected, claim_value=observed,
                          detail="Different scope assertions require review; this is not a legal applicability determination.")
                elif expected and not observed:
                    issue("claim_scope_unknown", ref=node.get("ref", candidate), field=key)
            has_counter = (node.get("counterargument") or any(link.get("relation") == "opposes"
                          for link in node.get("evidence", [])) or assessment["counterevidence_refs"])
            if has_counter and not (node.get("counterargument_response") or assessment["response"]):
                issue("unhandled_counterevidence", blocking=critical, ref=node.get("ref", candidate))
        # An assessment must explicitly describe the result of looking for
        # objections; the absence of an opposition link cannot prove none exist.
        if not assessment["response"] and not any(node.get("counterargument_response") for node in nodes):
            issue("countercheck_not_recorded", blocking=critical, ref=candidate)
        for condition in assessment["conditions"]:
            issue("assessment_condition", ref=candidate, detail=condition)
        for counter_ref in assessment["counterevidence_refs"]:
            try:
                obj = store.get_exact(counter_ref)
            except (ValueError, TypeError):
                issue("counterevidence_unavailable", blocking=critical, ref=counter_ref, claim_ref=candidate)
                continue
            if isinstance(obj, Artifact) and obj.kind == "legal_research":
                counter_report = research.inspect(store, counter_ref)
                reports.append(counter_report)
                if counter_report["stale"]:
                    stale = True
                    issue("counterevidence_stale", blocking=critical, ref=counter_ref, claim_ref=candidate,
                          issues=counter_report["issues"])
        assessments.append({**assessment, **direct_basis, "trusted_receipt_count": len(trusted),
                            "claim_kind": kind,
                            "premises_declared": assessment.get("premises_declared", False),
                            "premise_ids": assessment.get("premise_ids", []),
                            "effective_verdict": effective_verdict,
                            "needs_recheck": needs_recheck, "recheck_reasons": recheck_reasons,
                            "mechanical_binding": report["mechanical_binding"],
                            "research_issues": report["issues"], "semantic_verification": False})
    saved_answer = data.get("answer")
    answer_needs_refresh = bool(saved_answer and (saved_answer.get("needs_refresh")
                               or set(saved_answer.get("claim_refs", [])) & set(affected_claims)
                               or changed_answer_sources))
    if saved_answer:
        if answer_needs_refresh:
            issue("answer_needs_refresh", blocking=True,
                  claim_refs=[claim_ref for claim_ref in saved_answer.get("claim_refs", []) if claim_ref in affected_claims],
                  source_refs=list(dict.fromkeys(changed_answer_sources)))
        if not saved_answer["claim_refs"]:
            issue("answer_claims_missing", blocking=True)
        for gap in saved_answer.get("gaps", []):
            issue("answer_open_gap", blocking=True, detail=gap)
        for condition in saved_answer.get("conditions", []):
            issue("answer_condition", detail=condition)
    blockers = [value for value in issues if value["blocking"]]
    readiness = "open" if blockers else "conditional" if issues else "ready_for_review"
    blocked_tasks = {value.get("task_id") for value in blockers}
    next_steps = [{"premise_id": value["id"], "question": value["statement"], "statement": value["statement"],
                   "method": value["method"], "critical": bool(value["affected_claim_refs"]),
                   "next_step": value["next_check"], "if_true": value["if_true"], "if_false": value["if_false"],
                   "affected_claim_refs": value["affected_claim_refs"]}
                  for value in premise_reports if value["effective_status"] == "unknown" or value["needs_recheck"]]
    next_steps.extend({"task_id": item["id"], "question": item["question"], "critical": item["critical"],
                   "next_step": item["next_step"] or item["plan"] or "Investigate or recheck applicability, and bind actual tool receipts."}
                  for item in tasks if item["status"] != "resolved" or item["execution"] != "material_returned" or item["id"] in blocked_tasks)
    claim_checks = {}
    for value in blockers:
        target = value.get("claim_ref") or value.get("ref")
        if target in data["candidate_refs"]:
            claim_checks.setdefault(target, []).append(value["code"])
    for target, checks in claim_checks.items():
        next_steps.append({"claim_ref": target, "critical": True, "checks": list(dict.fromkeys(checks)),
                           "next_step": "Inspect the exact claim and source passages; test applicability and counterevidence, then revise or assess the claim with actual execution receipts."})
    missing_scope = [value["field"] for value in issues if value["code"] == "scope_unknown"]
    if missing_scope:
        next_steps.append({"scope_fields": missing_scope, "critical": False,
                           "next_step": "Clarify which scope facts could change the answer; record unknowns or explain why a field is not applicable."})
    return {"ref": ref, "current_ref": current, "readiness": readiness, "stale": stale,
            "blockers": blockers, "issues": issues, "next_steps": next_steps,
            "premises": premise_reports, "affected_claim_refs": list(dict.fromkeys(affected_claims)),
            "answer_needs_refresh": answer_needs_refresh,
            "tasks": tasks, "assessments": assessments, "dependencies": dependencies,
            "mechanical_binding": "invalid" if stale or any(value["code"] == "receipt_unavailable_or_changed" for value in issues) else "intact",
            "legal_verification": False, "semantic_verification": False,
            "note": "Readiness describes recorded work and open checks only. Tool execution, source binding, relevance, "
                    "search completeness and legal correctness are separate questions. Semantic assessments and scope remain assertions.",
            "research_reports": reports, "object": data}
