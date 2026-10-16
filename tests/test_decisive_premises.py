"""Decisive unknowns preserve uncertainty and invalidate only linked arguments."""
import copy
import json

import pytest

from core import inquiry, research
from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.records import Store


def save(store, artifact, prior=None):
    return store.put(artifact, supersedes=prior)


def source(store, text="The employer operates a local retail shop.", prior=None):
    return store.put(Record("document", {"text": Field(text, "extracted")}, "fixture", "employer"),
                     supersedes=prior)


def receipt(store, ref, identifier="source-1", **kwargs):
    return {"id": identifier, "tool": "file__extract", "category": "extract", "status": "ok",
            "executed": True, "refs": [{"ref": ref, "fingerprint": store.fingerprint(ref)}],
            **kwargs}


def claim(store, text="The provincial rule applies."):
    node = {"schema": research.SCHEMA, "type": "claim", "text": text,
            "status": "supported", "assessment": "model_assessed", "basis_kind": "assumption",
            "evidence": [], "premises": [], "scope": {"applicability": "asserted_applicable"},
            "counterargument": "", "counterargument_response": "Check the employer's actual industry.",
            "dependencies": []}
    return save(store, Artifact("legal_research", json.dumps(node),
                               Derivation((Field(text, "model"),), "fixture.claim")))


@pytest.fixture
def work():
    store = Store()
    primary = source(store)
    claims = [claim(store), claim(store, "The general minimum is not the full entitlement.")]
    ref = save(store, inquiry.frame(store, "Which employment rule applies?", candidate_refs=claims))
    return store, primary, claims, ref, [receipt(store, primary)]


def premise(work, ref=None, **kwargs):
    store, _, _, current, receipts = work
    current = ref or current
    defaults = {"statement": "The employer is provincially regulated.",
                "if_true": "Apply the provincial employment rule.",
                "if_false": "Check the federal or another applicable regime.",
                "next_check": "Identify the employer's actual industry.", "receipts": receipts}
    defaults.update(kwargs)
    return save(store, inquiry.premise(store, current, **defaults), current)


def assess(work, ref, selected=0, **kwargs):
    store, _, claims, _, receipts = work
    defaults = {"rationale": "Apply the rule only if its decisive premise holds.", "verdict": "supported",
                "response": "An industry exception could change applicability.", "receipts": receipts,
                "receipt_ids": [receipts[0]["id"]], "premise_ids": ["p1"]}
    defaults.update(kwargs)
    return save(store, inquiry.assess(store, ref, claims[selected], **defaults), ref)


def report(work, ref):
    return inquiry.inspect(work[0], ref, work[-1])


def test_unknown_premise_limits_inference_and_puts_decisive_question_first(work):
    ref = premise(work)
    ref = assess(work, ref)
    result = report(work, ref)
    p = result["premises"][0]
    checked = result["assessments"][0]
    assert p["id"] == "p1" and p["effective_status"] == "unknown"
    assert p["affected_claim_refs"] == [work[2][0]]
    assert checked["verdict"] == "supported" and checked["effective_verdict"] == "limited"
    assert not checked["needs_recheck"]
    assert result["next_steps"][0]["premise_id"] == "p1"
    assert result["next_steps"][0]["critical"]
    assert any(row["code"] == "premise_unknown" and row["blocking"] for row in result["issues"])


def test_premise_resolution_requires_explicit_reassessment_of_only_linked_claim(work):
    store, primary, claims, _, receipts = work
    ref = premise(work)
    ref = assess(work, ref)
    ref = assess(work, ref, selected=1, premise_ids=[], claim_kind="direct")
    ref = save(store, inquiry.answer(store, ref, "The answer currently depends on the industry.", claim_refs=claims), ref)
    original = ref
    ref = premise(work, ref, id="p1", status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]])
    result = report(work, ref)
    assert result["affected_claim_refs"] == [claims[0]]
    assert result["assessments"][0]["needs_recheck"]
    assert not result["assessments"][1]["needs_recheck"]
    assert result["answer_needs_refresh"]
    assert not result["object"]["answer"]["needs_refresh"]  # Inspection never rewrites the archive.
    ref = assess(work, ref)
    checked = next(row for row in report(work, ref)["assessments"] if row["claim_ref"] == claims[0])
    assert not checked["needs_recheck"] and checked["effective_verdict"] == "supported"
    assert inquiry.unpack(store.get_exact(original))["premises"][0]["status"] == "unknown"


def test_unrelated_saved_answer_and_claim_survive_another_premise_change(work):
    store, primary, claims, _, receipts = work
    ref = assess(work, premise(work))
    ref = assess(work, ref, selected=1, premise_ids=[], claim_kind="direct")
    ref = save(store, inquiry.answer(store, ref, "Minimum standards do not exhaust all rights.", claim_refs=[claims[1]]), ref)
    ref = premise(work, ref, id="p1", status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]])
    result = report(work, ref)
    assert not result["answer_needs_refresh"]
    assert not any(row["code"] == "answer_needs_refresh" for row in result["issues"])
    # Reassessing that unrelated argument must not invalidate the saved answer either.
    ref = assess(work, ref)
    assert not report(work, ref)["answer_needs_refresh"]


def test_source_revision_reopens_bound_premise_without_rebinding_to_new_material(work):
    store, primary, claims, _, receipts = work
    ref = premise(work, status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]])
    ref = assess(work, ref)
    ref = assess(work, ref, selected=1, premise_ids=[], claim_kind="direct")
    ref = save(store, inquiry.answer(store, ref, "Provincial rules apply on the stated evidence.", claim_refs=[claims[0]]), ref)
    newer = source(store, "The employer operates an interprovincial transport business.", prior=primary)
    result = report(work, ref)
    assert result["premises"][0]["needs_recheck"]
    assert result["premises"][0]["effective_status"] == "unknown"
    assert result["premises"][0]["evidence_refs"] == [primary]
    assert "premise_source_revised" in result["assessments"][0]["recheck_reasons"]
    assert result["affected_claim_refs"] == [claims[0]] and result["answer_needs_refresh"]
    assert not result["assessments"][1]["needs_recheck"]
    # Binding the new material changes only p1; explicit reassessment is still required.
    receipts.append(receipt(store, newer, "source-2"))
    ref = premise(work, ref, id="p1", status="refuted", evidence_refs=[newer], receipt_ids=["source-2"])
    assert report(work, ref)["assessments"][0]["needs_recheck"]
    ref = assess(work, ref)
    checked = next(row for row in report(work, ref)["assessments"] if row["claim_ref"] == claims[0])
    assert not checked["needs_recheck"] and checked["effective_verdict"] == "limited"


@pytest.mark.parametrize("failure", ["absent", "wrong_source", "no_match", "failed", "not_executed", "function", "fingerprint"])
def test_source_premise_rejects_missing_or_unrelated_execution(work, failure):
    store, primary, _, current, receipts = work
    entry = copy.deepcopy(receipts[0])
    ids = [entry["id"]]
    if failure == "absent":
        ids = []
    elif failure == "wrong_source":
        other = source(store, "Unrelated material")
        entry["refs"] = [{"ref": other, "fingerprint": store.fingerprint(other)}]
    elif failure == "no_match":
        entry["status"] = "no_match"
    elif failure == "failed":
        entry["status"] = "error"
    elif failure == "not_executed":
        entry["executed"] = False
    elif failure == "function":
        entry["category"] = "function"
    else:
        entry["refs"][0]["fingerprint"] = "invented"
    before = len(store.all())
    with pytest.raises(ValueError):
        premise(work, status="supported", evidence_refs=[primary], receipt_ids=ids, receipts=[entry])
    assert len(store.all()) == before and store.current_ref(current) == current


def test_generated_artifact_and_model_record_cannot_become_premise_evidence(work):
    store, _, claims, _, _ = work
    invented = store.put(Record("document", {"text": Field("The employer is provincial.", "model")}, "model", "fake"))
    for ref in [claims[0], invented]:
        with pytest.raises(ValueError):
            premise(work, status="supported", evidence_refs=[ref], receipt_ids=["made-up"],
                    receipts=[receipt(store, ref, "made-up")])


def test_read_receipt_must_bind_original_text_and_unchanged_observation(work):
    store, primary, _, _, receipts = work
    text = store.get_exact(primary).fields["text"].value
    observation = {"ref": primary, "field": "text", "offset": 0, "end": len(text),
                   "origin": "extracted", "text_sha256": research.digest(text)}
    receipts[0].update(category="read", tool="record__read", observations=[observation])
    ref = premise(work, status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]])
    assert report(work, ref)["premises"][0]["effective_status"] == "supported"
    observation["text_sha256"] = "changed"
    assert report(work, ref)["premises"][0]["needs_recheck"]
    with pytest.raises(ValueError, match="original-text read"):
        premise(work, ref, id="p1", status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]])


def test_user_report_requires_exact_user_quote_and_remains_user_reported(work):
    with pytest.raises(ValueError, match="exact passage"):
        premise(work, method="user", status="supported", user_quote="The employer is federal.",
                user_texts=["I work in a local retail shop."])
    with pytest.raises(ValueError, match="exact user_quote"):
        premise(work, method="user", status="supported")
    ref = premise(work, method="user", status="supported", user_quote="local retail shop",
                  user_texts=["I work in a local retail shop."])
    value = report(work, ref)["premises"][0]
    assert value["basis"] == "user_reported" and value["assessment"] == "model_assessed"
    assert not value["semantic_verification"] and not value["legal_verification"]
    assert value["user_binding"]["quote_sha256"] == research.digest("local retail shop")


def test_hypothesis_cannot_report_supported_even_when_no_unknowns_are_declared(work):
    ref = assess(work, work[3], premise_ids=[], claim_kind="hypothesis")
    result = report(work, ref)
    assert result["assessments"][0]["effective_verdict"] == "limited"
    assert any(row["code"] == "hypothesis_not_established" for row in result["issues"])


def test_legacy_snapshot_without_premises_or_kind_is_readable_and_explicitly_undeclared(work):
    store, _, _, ref, _ = work
    ref = assess(work, ref, premise_ids=None)
    data = inquiry.unpack(store.get_exact(ref))
    data.pop("premises")
    for key in ("claim_kind", "premise_ids", "premise_snapshots", "premises_declared"):
        data["assessments"][0].pop(key)
    ref = save(store, inquiry.pack(data), ref)
    result = report(work, ref)
    assert result["premises"] == []
    assert not result["assessments"][0]["premises_declared"]
    assert result["assessments"][0]["effective_verdict"] == "supported"
    assert not result["assessments"][0]["needs_recheck"]


def test_new_unbound_material_and_search_plan_only_edits_do_not_invalidate_claim(work):
    store, primary, _, _, receipts = work
    ref = premise(work, status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]])
    ref = assess(work, ref)
    source(store, "An unrelated later document.")
    ref = premise(work, ref, id="p1", status="supported", evidence_refs=[primary], receipt_ids=[receipts[0]["id"]],
                  next_check="If additional doubt arises, ask for the employer's public registration.")
    assert not report(work, ref)["assessments"][0]["needs_recheck"]


def test_bound_premise_id_must_exist_and_unbound_unknown_is_still_actionable(work):
    with pytest.raises(ValueError, match="Unknown premise ID"):
        assess(work, work[3])
    result = report(work, premise(work))
    assert result["next_steps"][0]["premise_id"] == "p1"
    assert not result["next_steps"][0]["critical"]
