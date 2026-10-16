"""Calling a proposition direct cannot promote interpretation to a source fact."""
import json

import pytest

from core import inquiry, research
from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.records import Store


def artifact(store, node):
    text = json.dumps({"schema": research.SCHEMA, **node})
    return store.put(Artifact("legal_research", text, Derivation((Field(text, "model"),), "fixture.v1")))


def claim_with_quote(store, quote, conclusion=None, *, relation="supports", origin="extracted", mismatch=False):
    source_ref = store.put(Record("document", {"text": Field(quote, origin)}, "fixture", "rule"))
    excerpt_ref = artifact(store, {"type": "excerpt", "quote": quote, "speaker": "source",
        "source": {"ref": source_ref, "fingerprint": store.fingerprint(source_ref), "field": "text",
                   "work": {"provider": "fixture", "record_id": "rule", "source_type": "document"},
                   "start": 0, "end": len(quote), "field_sha256": research.digest(quote),
                   "quote_sha256": "wrong" if mismatch else research.digest(quote), "origin": origin},
        "dependencies": [research.dependency(store, source_ref, "source")]})
    claim_ref = artifact(store, {"type": "claim", "text": conclusion if conclusion is not None else quote,
        "status": "supported", "basis_kind": "source", "assessment": "model_assessed",
        "evidence": [{"excerpt_ref": excerpt_ref, "relation": relation}], "premises": [],
        "counterargument": "", "counterargument_response": "Only the cited material was checked.",
        "dependencies": [research.dependency(store, excerpt_ref, relation)]})
    return source_ref, excerpt_ref, claim_ref


def assessed(store, claim_ref, *, kind="direct"):
    initial = store.put(inquiry.frame(store, "What does the evidence establish?"))
    updated = store.put(inquiry.assess(store, initial, claim_ref, "Check its actual wording.",
                        verdict="supported", claim_kind=kind), supersedes=initial)
    return updated, inquiry.inspect(store, updated)


def test_exact_supported_original_quote_records_its_mechanical_basis():
    store = Store()
    _, excerpt_ref, claim_ref = claim_with_quote(store, "The measured count declined from 100 to 80.")
    ref, report = assessed(store, claim_ref)
    row = report["assessments"][0]
    assert row["direct_basis"] == "source_quote" and row["direct_excerpt_refs"] == [excerpt_ref]
    assert row["effective_verdict"] == "supported"
    assert inquiry.unpack(store.get_exact(ref))["assessments"][0]["direct_basis"] == "source_quote"
    assert row["semantic_verification"] is False


@pytest.mark.parametrize("conclusion", [
    "The policy caused the decline.", "The policy probably explains part of the decline.",
    "The measured count fell by 20%.", "The count declined.",
])
def test_interpretations_or_calculations_mislabeled_direct_remain_interpretations(conclusion):
    store = Store()
    _, _, claim_ref = claim_with_quote(store, "The measured count declined from 100 to 80.", conclusion)
    _, report = assessed(store, claim_ref)
    row = report["assessments"][0]
    assert row["direct_basis"] == "model_interpretation"
    assert row["verdict"] == "supported" and row["effective_verdict"] == "limited"
    issue = next(value for value in report["issues"] if value["code"] == "direct_claim_interpretation")
    assert not issue["blocking"]


@pytest.mark.parametrize("settings", [{"relation": "opposes"}, {"origin": "model"}, {"mismatch": True}])
def test_exact_text_alone_cannot_invent_a_valid_supporting_source(settings):
    store = Store()
    _, _, claim_ref = claim_with_quote(store, "The policy caused the decline.", **settings)
    _, report = assessed(store, claim_ref)
    assert report["assessments"][0]["direct_basis"] == "model_interpretation"
    assert report["assessments"][0]["effective_verdict"] == "limited"


def test_inspection_recomputes_basis_instead_of_trusting_a_stored_direct_label():
    store = Store()
    _, _, claim_ref = claim_with_quote(store, "Two events occurred together.", "One caused the other.")
    ref, _ = assessed(store, claim_ref)
    node = inquiry.unpack(store.get_exact(ref))
    node["assessments"][0].update(direct_basis="source_quote", direct_excerpt_refs=["made-up"])
    changed = store.put(inquiry.pack(node), supersedes=ref)
    row = inquiry.inspect(store, changed)["assessments"][0]
    assert row["direct_basis"] == "model_interpretation" and row["direct_excerpt_refs"] == []


@pytest.mark.parametrize("kind,expected", [("inference", "supported"), ("hypothesis", "limited")])
def test_other_claim_kinds_keep_existing_behavior(kind, expected):
    store = Store()
    _, _, claim_ref = claim_with_quote(store, "Two events occurred together.", "One caused the other.")
    _, report = assessed(store, claim_ref, kind=kind)
    row = report["assessments"][0]
    assert "direct_basis" not in row and row["effective_verdict"] == expected
    assert not any(value["code"] == "direct_claim_interpretation" for value in report["issues"])


@pytest.mark.parametrize("kind", ["observations", "calculations"])
def test_independent_answer_evidence_is_saved_and_source_revision_reopens_only_that_answer(kind):
    store = Store()
    source_ref = store.put(Record("document", {"text": Field("100 then 80.", "extracted")}, "fixture", "counts"))
    unrelated_ref = store.put(Record("document", {"text": Field("Other material.", "extracted")}, "fixture", "other"))
    initial = store.put(inquiry.frame(store, "What changed?"))
    delivery = {kind: [{"text": "100 then 80.", "provenance": {"source_ref": source_ref}}],
                "source_checks": [{"url": "https://example.com/rule", "status": "failed"}]}
    answered = store.put(inquiry.answer(store, initial, "The reported count fell by 20%.", delivery_evidence=delivery), supersedes=initial)
    delivery[kind][0]["provenance"]["source_ref"] = "mutated-after-save"
    node = inquiry.unpack(store.get_exact(answered))
    assert node["answer"]["delivery_evidence"][kind][0]["provenance"]["source_ref"] == source_ref
    assert any(value["ref"] == source_ref and value["role"] == "answer_source" for value in node["dependencies"])
    store.put(Record("document", {"text": Field("Updated unrelated material.", "extracted")}, "fixture", "other"), supersedes=unrelated_ref)
    assert not inquiry.inspect(store, answered)["answer_needs_refresh"]
    store.put(Record("document", {"text": Field("100 then 90.", "extracted")}, "fixture", "counts"), supersedes=source_ref)
    report = inquiry.inspect(store, answered)
    assert report["answer_needs_refresh"] and report["affected_claim_refs"] == []
    assert next(row for row in report["issues"] if row["code"] == "answer_needs_refresh")["source_refs"] == [source_ref]


def test_user_reported_calculation_without_source_ref_does_not_invent_a_source_dependency():
    store = Store()
    initial = store.put(inquiry.frame(store, "What changed?"))
    answered = store.put(inquiry.answer(store, initial, "The stated figures imply a 20% decline.",
        delivery_evidence={"calculations": [{"provenance": {"kind": "user_reported", "quote": "100 then 80"}}]}), supersedes=initial)
    report = inquiry.inspect(store, answered)
    assert report["dependencies"] == [] and not report["answer_needs_refresh"]
