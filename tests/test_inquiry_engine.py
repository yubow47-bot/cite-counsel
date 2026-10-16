"""Substantive investigation regressions: execution is not legal correctness."""

import json

import pytest

from core import inquiry, research
from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.records import Store


SCOPE = {"jurisdiction": "Synthetic Province", "as_of": "2026-10-15",
         "procedure": "administrative review", "factual_context": "notice sent, receipt unknown"}


@pytest.fixture
def work():
    store = Store()
    text = "A notice is effective on receipt. An exception applies to electronic service."
    source = store.put(Record("legislation", {"full_text": Field(text, "database", source_id="synthetic:rule")},
                              "synthetic", "rule"))
    node = {"schema": research.SCHEMA, "type": "excerpt", "quote": text, "speaker": "author",
            "source": {"ref": source, "fingerprint": store.fingerprint(source), "work": {
                "provider": "synthetic", "record_id": "rule", "source_type": "legislation"},
                "field": "full_text", "start": 0, "end": len(text), "field_sha256": research.digest(text),
                "quote_sha256": research.digest(text), "origin": "database", "source_id": "synthetic:rule"},
            "dependencies": [research.dependency(store, source, "source")]}
    excerpt = store.put(Artifact("legal_research", json.dumps(node), Derivation((Field(text, "model"),), "test.excerpt")))
    claim = save_claim(store, excerpt)
    receipt = {"id": "trusted-search-1", "tool": "synthetic__search", "category": "source", "status": "ok",
               "refs": [{"ref": source, "fingerprint": store.fingerprint(source)}], "sequence": 1,
               "coverage": {"complete": True}, "error_kind": None}
    return store, source, excerpt, claim, [receipt]


def save_claim(store, excerpt, *, status="supported", scope=None, opposing=False, counterargument="", prior=None):
    node = {"schema": research.SCHEMA, "type": "claim", "text": "Receipt, rather than dispatch alone, matters.",
            "status": status, "assessment": "model_assessed", "basis_kind": "source",
            "evidence": [{"excerpt_ref": excerpt, "relation": "supports", "note": "asserted relevance"}],
            "premises": [], "scope": {**(SCOPE if scope is None else scope), "applicability": "asserted_applicable"},
            "counterargument": counterargument, "counterargument_response": "",
            "dependencies": [research.dependency(store, excerpt, "supports")]}
    if opposing:
        node["evidence"].append({"excerpt_ref": excerpt, "relation": "opposes", "note": "electronic-service exception"})
    return store.put(Artifact("legal_research", json.dumps(node), Derivation((Field(node["text"], "model"),), "test.claim")),
                     supersedes=prior)


def save(store, artifact, prior=None):
    return store.put(artifact, supersedes=prior)


def framed(work, **kwargs):
    store, _, _, claim, _ = work
    return save(store, inquiry.frame(store, "Was dispatch enough, and what should I establish next?", scope=SCOPE,
                                    candidate_refs=[claim], **kwargs))


def investigated(work, ref=None):
    store, _, _, _, receipts = work
    ref = ref or framed(work)
    return save(store, inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=[receipts[0]["id"]],
                                     findings="The rule distinguishes receipt and dispatch.", receipts=receipts), ref)


def assessed(work, ref=None, **kwargs):
    store, _, _, claim, receipts = work
    ref = ref or investigated(work)
    defaults = {"rationale": "The passage supports this limited reading, subject to the service exception.",
                "verdict": "supported", "scope": SCOPE,
                "response": "Checked the stated exception; receipt facts remain to be established.",
                "receipt_ids": [receipts[0]["id"]], "receipts": receipts}
    defaults.update(kwargs)
    return save(store, inquiry.assess(store, ref, claim, **defaults), ref)


def codes(report):
    return {item["code"] for item in report["issues"]}


def test_framing_keeps_missing_scope_explicit_and_question_open(work):
    store, *_ = work
    ref = save(store, inquiry.frame(store, "Can I appeal?"))
    report = inquiry.inspect(store, ref)
    assert report["object"]["scope"] == dict.fromkeys(inquiry.SCOPE_FIELDS)
    assert report["readiness"] == "open"
    assert "critical_task_open" in codes(report)
    assert "candidate_explanations_missing" in codes(report)
    assert not report["legal_verification"]


def test_model_cannot_complete_planned_or_fabricated_investigation(work):
    store, *_ = work
    ref = framed(work)
    with pytest.raises(ValueError, match="trusted tool receipts"):
        inquiry.pursue(store, ref, "q1", status="resolved", findings="I searched everywhere.")
    with pytest.raises(ValueError, match="Unknown trusted tool receipt"):
        inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=["invented"], receipts=work[-1])
    plan = save(store, inquiry.pursue(store, ref, "q1", plan="Find the service rule."), ref)
    assert inquiry.inspect(store, plan)["tasks"][0]["execution"] == "not_executed"


def test_successful_self_written_claim_is_not_external_investigation(work):
    store, _, _, claim, receipts = work
    internal = {**receipts[0], "id": "internal", "tool": "research__claim", "category": "function",
                "refs": [{"ref": claim, "fingerprint": store.fingerprint(claim)}]}
    ref = framed(work)
    ref = save(store, inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=["internal"], receipts=[internal]), ref)
    report = inquiry.inspect(store, ref, [internal])
    assert "investigation_not_executed" in codes(report)
    assert report["readiness"] == "open"


@pytest.mark.parametrize("status,expected,unexpected", [
    ("no_match", "search_no_match", "source_execution_failed"),
    ("error", "source_execution_failed", "search_no_match"),
])
def test_true_no_match_and_execution_failure_have_different_next_work(work, status, expected, unexpected):
    store, _, _, _, receipts = work
    attempt = {**receipts[0], "status": status, "refs": [], "error_kind": "timeout" if status == "error" else None}
    ref = framed(work)
    ref = save(store, inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=[attempt["id"]], receipts=[attempt]), ref)
    report = inquiry.inspect(store, ref, [attempt])
    assert expected in codes(report) and unexpected not in codes(report)
    assert report["readiness"] == "open"
    assert report["next_steps"]


def test_retry_retains_failure_and_uses_new_trusted_material(work):
    store, _, _, _, receipts = work
    failure = {**receipts[0], "id": "failed-attempt", "status": "error", "refs": [], "error_kind": "timeout"}
    table = [failure, *receipts]
    ref = framed(work)
    ref = save(store, inquiry.pursue(store, ref, "q1", status="blocked", receipt_ids=[failure["id"]], receipts=table), ref)
    ref = save(store, inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=[receipts[0]["id"]], receipts=table), ref)
    report = inquiry.inspect(store, ref, table)
    assert len(report["tasks"][0]["attempts"]) == 2
    assert report["tasks"][0]["execution"] == "material_returned"
    assert "source_failure_recovered" in codes(report)
    assert "source_execution_failed" not in codes(report)


def test_previous_success_does_not_hide_the_latest_failed_attempt(work):
    store, _, _, _, receipts = work
    ref = investigated(work)
    failure = {**receipts[0], "id": "latest-failure", "status": "error", "refs": [], "error_kind": "timeout"}
    table = [*receipts, failure]
    ref = save(store, inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=[failure["id"]], receipts=table), ref)
    report = inquiry.inspect(store, ref, table)
    assert report["tasks"][0]["execution"] == "failed"
    assert "source_execution_failed" in codes(report)
    assert "source_failure_recovered" not in codes(report)


def test_successful_material_does_not_silence_a_failed_parallel_source(work):
    store, _, _, _, receipts = work
    failure = {**receipts[0], "id": "parallel-failure", "status": "error", "refs": [], "error_kind": "timeout"}
    table = [*receipts, failure]
    ref = framed(work)
    ref = save(store, inquiry.pursue(store, ref, "q1", status="resolved", receipt_ids=[item["id"] for item in table], receipts=table), ref)
    report = inquiry.inspect(store, ref, table)
    assert report["tasks"][0]["execution"] == "partial_failure"
    assert "source_execution_failed" in codes(report)


def test_support_binding_never_proves_semantics_and_countercheck_requires_execution(work):
    store, _, _, _, receipts = work
    ref = assessed(work, receipt_ids=[])
    report = inquiry.inspect(store, ref, receipts)
    assert report["mechanical_binding"] == "intact"
    assert "countercheck_not_executed" in codes(report)
    assert not report["semantic_verification"] and not report["legal_verification"]


def test_handled_opposing_material_is_preserved_as_a_condition(work):
    store, _, excerpt, _, receipts = work
    claim = save_claim(store, excerpt, opposing=True)
    local = (store, work[1], excerpt, claim, receipts)
    ref = investigated(local)
    unhandled = assessed(local, ref=ref, response="")
    assert "unhandled_counterevidence" in codes(inquiry.inspect(store, unhandled, receipts))
    handled = assessed(local, ref=unhandled, response="The exception may apply only if electronic service is established.")
    report = inquiry.inspect(store, handled, receipts)
    assert "unhandled_counterevidence" not in codes(report)
    assert "opposing_evidence_present" in codes(report)
    assert report["readiness"] == "conditional"


def test_scope_mismatch_and_disputed_research_block_unconditional_synthesis(work):
    store, _, excerpt, _, receipts = work
    claim = save_claim(store, excerpt, status="disputed", scope={**SCOPE, "jurisdiction": "Other Province"})
    local = (store, work[1], excerpt, claim, receipts)
    ref = assessed(local)
    report = inquiry.inspect(store, ref, receipts)
    assert {"scope_mismatch", "unresolved_disputed"} <= codes(report)
    assert report["readiness"] == "open"


def test_source_revision_reopens_completed_reasoning_and_preserves_exact_versions(work):
    store, source, _, claim, receipts = work
    ref = assessed(work)
    ref = save(store, inquiry.answer(store, ref, "Receipt needs to be established.", claim_refs=[claim]), ref)
    assert inquiry.inspect(store, ref, receipts)["readiness"] == "ready_for_review"
    replacement = store.put(Record("legislation", {"full_text": Field("Revised service rule.", "database", source_id="synthetic:rule")},
                                   "synthetic", "rule"), supersedes=source)
    report = inquiry.inspect(store, ref, receipts)
    assert report["stale"] and report["readiness"] == "open"
    assert "claim_stale" in codes(report)
    assert report["object"]["answer"]["claim_refs"] == [claim]
    assert report["dependencies"][1]["ref"] == source
    assert replacement != source


def test_trusted_receipts_cannot_be_rebound_to_other_work_or_changed_after_save(work):
    store, _, _, _, receipts = work
    ref = investigated(work)
    changed = [{**receipts[0], "refs": []}]
    assert "receipt_unavailable_or_changed" in codes(inquiry.inspect(store, ref, changed))
    corrupt = [{**receipts[0], "refs": [{"ref": receipts[0]["refs"][0]["ref"], "fingerprint": "forged"}]}]
    with pytest.raises(ValueError, match="fingerprint"):
        inquiry.pursue(store, ref, "q1", receipt_ids=[receipts[0]["id"]], receipts=corrupt)


def test_stale_prior_conflicts_and_later_work_invalidates_saved_answer(work):
    store, _, _, claim, receipts = work
    ref = assessed(work)
    final = save(store, inquiry.answer(store, ref, "A conditional answer.", claim_refs=[claim]), ref)
    with pytest.raises(ValueError, match="revision conflict"):
        inquiry.pursue(store, ref, "q1", plan="An obsolete edit.")
    revised = save(store, inquiry.pursue(store, final, "q1", status="investigating", plan="Check service method."), final)
    report = inquiry.inspect(store, revised, receipts)
    assert "answer_needs_refresh" in codes(report)
    assert report["object"]["answer"]["text"] == "A conditional answer."


def test_assessment_cannot_depend_on_inquiry_it_is_replacing(work):
    store, _, _, claim, receipts = work
    ref = framed(work)
    with pytest.raises(ValueError, match="version it replaces"):
        inquiry.assess(store, ref, claim, "A purported objection.", counterevidence_refs=[ref], receipts=receipts)


def test_clarification_preserves_work_but_reopens_changed_scope_and_question(work):
    store, _, _, _, receipts = work
    original = investigated(work)
    ref = save(store, inquiry.refine(store, original, scope={"jurisdiction": "Other Province"},
                                     questions=[{"question": "Was service electronic?", "why": "The exception may change the answer."}]), original)
    report = inquiry.inspect(store, ref, receipts)
    assert report["object"]["scope"]["procedure"] == SCOPE["procedure"]
    assert report["object"]["scope"]["jurisdiction"] == "Other Province"
    assert len(report["tasks"]) == 2
    assert report["tasks"][0]["trusted_receipt_count"] == 1
    assert "task_scope_changed" in codes(report)
    changed = save(store, inquiry.refine(store, ref, questions=[{"id": "q1", "question": "When did receipt occur?"}]), ref)
    task = inquiry.inspect(store, changed, receipts)["tasks"][0]
    assert task["status"] == "open" and task["execution"] == "not_executed"
    assert task["trusted_receipt_count"] == 1 and len(task["attempts"]) == 2


def test_missing_counterevidence_returns_open_check_instead_of_hiding_report(work):
    store, _, _, _, receipts = work
    other = store.put(Record("document", {"text": Field("A conflicting account", "extracted")}, "fixture", "counter"))
    ref = assessed(work, counterevidence_refs=[other])
    # Simulate a partially recoverable damaged archive; normal UI has no
    # per-object delete, but one unavailable dependency must not hide others.
    del store._objects[other]
    store._fingerprints.pop(other, None)
    report = inquiry.inspect(store, ref, receipts)
    assert "counterevidence_unavailable" in codes(report)
    assert "dependency_unavailable" in codes(report)
    assert report["readiness"] == "open"


def test_exact_read_observations_remain_available_and_cannot_change_after_binding(work):
    store, source, _, _, receipts = work
    observation = {"ref": source, "field": "full_text", "offset": 2, "end": 20,
                   "origin": "database", "total_chars": 75, "text_sha256": research.digest("the actual passage")}
    receipts[0]["observations"] = [observation]
    ref = investigated(work)
    report = inquiry.inspect(store, ref, receipts)
    saved = report["tasks"][0]["receipts"][0]["observations"]
    assert saved == [observation]
    # A different observed span is a different execution binding even when
    # the whole immutable Record fingerprint remains unchanged.
    receipts[0]["observations"][0]["offset"] = 30
    assert "receipt_unavailable_or_changed" in codes(inquiry.inspect(store, ref, receipts))
