"""A prose review can discover unknowns but cannot certify or discard them."""
import json
import copy
import hashlib

import pytest

from harness.core import Harness
from harness.plugin import discover
from plugins import inquiry as plugin
from plugins.inquiry.review import review_candidate


@pytest.fixture
def ctx(tmp_path):
    h = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    session.messages.append({"role": "user", "content": "The policy preceded the decline. Did it cause the decline?"})
    return h.context(session, "inquiry")


def premise(**updates):
    value = {"statement": "The comparison group estimates the untreated outcome for this group.",
             "if_true": "The comparison can inform the counterfactual if the other conditions hold.",
             "if_false": "The comparison does not identify the policy's causal effect.",
             "next_check": "Compare pre-policy trends and treatment assignment.", "method": "source"}
    value.update(updates)
    return value


def params(**changes):
    check = {"conclusion": "The comparison does not by itself establish a causal effect.",
             "passages": [{"source_ref": "rec_1", "quote": "A comparison requires assumptions."}],
             "reasoning": "An untreated counterfactual is required.",
             "objection": "The groups may differ before treatment.",
             "response": "That possibility is untested.", "verdict": "limited",
             "claim_kind": "inference", "task_ids": ["causality"], "premise_ids": ["p1"],
             "premises": [premise(id="p2")]}
    check.update(changes)
    return plugin.ConcludeParams(calculations=[], answer="Causation is not established.", checks=[check])


def edited(original, **changes):
    check = {name: getattr(original.checks[0], name)
             for name in ("conclusion", "reasoning", "objection", "response", "verdict")}
    check.update(changes)
    return {"answer": "A conditional revised answer.", "checks": [check], "corrections": []}


def review(ctx, monkeypatch, original, output, delivery_evidence=None):
    payloads = []

    def request(messages):
        payloads.append(json.loads(messages[1]["content"]))
        return {"content": json.dumps(output)}, {"status": "model_reviewed", "semantic_verification": False}

    monkeypatch.setattr(ctx, "request_review", request)
    updated, audit = review_candidate(ctx, original, [], delivery_evidence=delivery_evidence)
    return updated, audit, payloads


def test_review_adds_a_specific_unknown_and_preserves_all_existing_bindings(ctx, monkeypatch):
    original = params()
    new_premise = premise(statement="Both series use the same population and measurement window.",
                          next_check="Read the population definitions and observation dates for both series.")
    updated, audit, payloads = review(ctx, monkeypatch, original,
                                    edited(original, premises=[new_premise]))

    before, after = original.checks[0], updated.checks[0]
    assert audit["status"] == "model_reviewed"
    assert after.premises[0].model_dump() == before.premises[0].model_dump()
    added = after.premises[1]
    assert added.status == "unknown" and added.id == ""
    assert added.evidence_refs == [] and added.user_quote == ""
    assert added.statement == new_premise["statement"]
    assert after.passages == before.passages and after.task_ids == before.task_ids
    assert after.premise_ids == before.premise_ids
    assert payloads[0]["checks"][0]["premises"][0]["id"] == "p2"
    assert payloads[0]["checks"][0]["premise_ids"] == ["p1"]
    assert len(payloads) == 1


def test_omitted_review_verdict_keeps_the_correction_with_a_limited_assessment(ctx, monkeypatch):
    original = params(verdict="supported")
    output = edited(original)
    del output["checks"][0]["verdict"]
    updated, audit, _ = review(ctx, monkeypatch, original, output)
    assert audit["status"] == "model_reviewed"
    assert updated.answer == output["answer"]
    assert audit["reviewed_answer_sha256"] == hashlib.sha256(updated.answer.encode()).hexdigest()
    assert updated.checks[0].verdict == "limited"
    assert updated.checks[0].passages == original.checks[0].passages
    assert updated.checks[0].premises == original.checks[0].premises


@pytest.mark.parametrize("output_premises", [None, []])
def test_omission_or_empty_list_cannot_remove_input_premises(ctx, monkeypatch, output_premises):
    original = params(premises=[premise(status="supported", evidence_refs=["rec_3"])])
    changes = {} if output_premises is None else {"premises": output_premises}
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, **changes))
    assert audit["status"] == "model_reviewed"
    assert updated.checks[0].premises == original.checks[0].premises
    assert updated.checks[0].claim_kind == original.checks[0].claim_kind


def test_repeated_premise_corrects_only_branches_and_preserves_original_state(ctx, monkeypatch):
    original = params(premises=[premise(id="p2", status="supported", evidence_refs=["rec_3"])])
    repeated = premise(method="user", next_check="A different check that must not overwrite the original.",
                       if_true="The comparison may inform the counterfactual if the other identifying assumptions hold.",
                       if_false="The comparison does not identify the effect; an effect may still exist.")
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[repeated]))
    assert audit["status"] == "model_reviewed"
    before = original.checks[0].premises[0].model_dump()
    after = updated.checks[0].premises[0].model_dump()
    assert len(updated.checks[0].premises) == 1
    assert after["if_true"] == repeated["if_true"] and after["if_false"] == repeated["if_false"]
    assert {key: value for key, value in after.items() if key not in {"if_true", "if_false"}} == {
        key: value for key, value in before.items() if key not in {"if_true", "if_false"}}
    assert after["status"] == "supported" and after["method"] == "source"
    assert after["evidence_refs"] == ["rec_3"] and original.checks[0].premises[0].if_true == before["if_true"]
    assert any("repeated premise branches" in value for value in audit["corrections"])


@pytest.mark.parametrize("conflict", [False, True])
def test_repeated_premise_cannot_conflict_with_an_explicit_branch_edit(ctx, monkeypatch, conflict):
    original = params()
    branches = {"index": 0, "if_true": "The comparison may be useful under the remaining conditions.",
                "if_false": "The effect is still unestablished."}
    repeated = premise(if_true="A conflicting consequence." if conflict else branches["if_true"],
                       if_false=branches["if_false"])
    updated, audit, _ = review(ctx, monkeypatch, original, edited(
        original, premise_branches=[branches], premises=[repeated]))
    if conflict:
        assert updated is original and audit["status"] == "failed"
        assert "reviewed_answer_sha256" not in audit
    else:
        assert audit["status"] == "model_reviewed" and len(updated.checks[0].premises) == 1
        assert updated.checks[0].premises[0].if_true == branches["if_true"]


def test_repeated_premise_preserves_user_quote_and_its_method(ctx, monkeypatch):
    original = params(premises=[premise(method="user", status="supported", user_quote="The policy preceded the decline.")])
    repeated = premise(if_false="Temporal ordering would need to be checked before interpreting causation.")
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[repeated]))
    result = updated.checks[0].premises[0]
    assert audit["status"] == "model_reviewed" and result.method == "user"
    assert result.user_quote == original.checks[0].premises[0].user_quote
    assert result.status == "supported" and result.if_false == repeated["if_false"]


@pytest.mark.parametrize("status", ["unknown", "supported", "refuted"])
def test_full_inline_premise_echo_accepts_exact_readonly_bindings_and_keeps_original_state(ctx, monkeypatch, status):
    original = params(premises=[premise(id="p2", method="user", status=status,
        evidence_refs=["rec_3"], user_quote="The policy preceded the decline.")])
    repeated = original.checks[0].premises[0].model_dump()
    repeated["if_true"] = "Temporal ordering alone does not establish a causal effect."
    before = original.model_dump()
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[repeated]))
    assert audit["status"] == "model_reviewed" and len(updated.checks[0].premises) == 1
    new = updated.checks[0].premises[0].model_dump()
    assert new["if_true"] == repeated["if_true"]
    for key in ("id", "evidence_refs", "user_quote", "status", "method"):
        assert new[key] == original.checks[0].premises[0].model_dump()[key]
    assert original.model_dump() == before


@pytest.mark.parametrize("key,value", [("id", "other-id"), ("evidence_refs", ["rec_999"]),
                                      ("user_quote", "Invented user wording."), ("status", "unknown")])
def test_full_inline_premise_echo_rejects_any_changed_readonly_binding(ctx, monkeypatch, key, value):
    original = params(premises=[premise(id="p2", method="user", status="supported",
        evidence_refs=["rec_3"], user_quote="The policy preceded the decline.")])
    repeated = original.checks[0].premises[0].model_dump()
    repeated[key] = value
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[repeated]))
    assert updated is original and audit["status"] == "failed"
    assert "reviewed_answer_sha256" not in audit


@pytest.mark.parametrize("statement", ["A replacement proposition.", None])
def test_readonly_echo_cannot_use_identity_to_rewrite_or_ambiguously_match_a_statement(ctx, monkeypatch, statement):
    original = params(premises=[premise(id="p2"), premise(id="p3")]) if statement is None else params()
    repeated = original.checks[0].premises[0].model_dump()
    if statement is not None:
        repeated["statement"] = statement
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[repeated]))
    assert updated is original and audit["status"] == "failed"


def test_new_premise_cannot_smuggle_reference_fields_even_if_empty(ctx, monkeypatch):
    original = params()
    added = premise(statement="A newly discovered condition.", id="", evidence_refs=[], user_quote="")
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[added]))
    assert updated is original and audit["status"] == "failed"


def test_repeated_premise_must_not_choose_between_ambiguous_original_statements(ctx, monkeypatch):
    original = params(premises=[premise(id="p2"), premise(id="p3")])
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[premise(if_true="A revised consequence.")]))
    assert updated is original and audit["status"] == "failed"


def test_duplicate_new_premise_with_conflicting_branches_is_rejected(ctx, monkeypatch):
    original = params()
    added = premise(statement="The populations use a comparable measurement definition.")
    conflicting = {**added, "if_false": "A different consequence."}
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, premises=[added, conflicting]))
    assert updated is original and audit["status"] == "failed"


@pytest.mark.parametrize("status", ["supported", "refuted"])
def test_reviewer_cannot_resolve_a_premise(ctx, monkeypatch, status):
    original = params()
    updated, audit, payloads = review(ctx, monkeypatch, original,
                                    edited(original, premises=[premise(status=status)]))
    assert updated is original and audit["status"] == "failed"
    assert original.checks[0].premises[0].status == "unknown"
    assert len(payloads) == 1


@pytest.mark.parametrize("kind", ["inference", "hypothesis"])
def test_review_cannot_relabel_inference_or_hypothesis_as_direct(ctx, monkeypatch, kind):
    original = params(claim_kind=kind)
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, claim_kind="direct"))
    assert audit["status"] == "model_reviewed"
    assert updated.checks[0].claim_kind == kind
    assert any("classification" in correction for correction in audit["corrections"])


def test_review_can_recognize_that_a_direct_input_requires_an_unknown_premise(ctx, monkeypatch):
    original = params(claim_kind="direct", premises=[])
    updated, audit, _ = review(ctx, monkeypatch, original,
                              edited(original, claim_kind="inference", premises=[premise()]))
    assert audit["status"] == "model_reviewed"
    assert updated.checks[0].claim_kind == "inference"
    assert updated.checks[0].premises[0].status == "unknown"


def test_too_many_additions_keep_known_premises_and_list_the_extra_as_a_gap(ctx, monkeypatch):
    # Live runs lost every review correction when a reviewer added one
    # condition too many. Known premises still come first and are all kept;
    # the extra condition stays visible as an open gap.
    original = params(premises=[premise(statement=f"Existing condition {i}.") for i in range(3)])
    updated, audit, _ = review(ctx, monkeypatch, original,
                              edited(original, premises=[premise(statement="Another condition.")]))
    assert audit["status"] == "model_reviewed"
    assert [value.statement for value in updated.checks[0].premises] == [f"Existing condition {i}." for i in range(3)]
    assert [value.model_dump() for value in updated.checks[0].premises] == [value.model_dump() for value in original.checks[0].premises]
    assert any(gap.startswith("Unverified condition: Another condition.") for gap in updated.gaps)


@pytest.mark.parametrize("extra", [{"premise_ids": []}, {"task_ids": []}, {"passages": []}])
def test_review_output_cannot_rewrite_saved_reference_lists(ctx, monkeypatch, extra):
    original = params()
    updated, audit, _ = review(ctx, monkeypatch, original, edited(original, **extra))
    assert updated is original and audit["status"] == "failed"


def test_review_context_explains_linked_saved_premises_without_receipt_logs(ctx, monkeypatch):
    from core import inquiry

    current = ctx.save(inquiry.frame(ctx.records, "Did the policy cause the decline?"))
    for identifier in ("p1", "unrelated"):
        current = ctx.save(inquiry.premise(ctx.records, current, id=identifier, **premise()), supersedes=current)
    original = params(premises=[])
    original.ref = current
    _, audit, payloads = review(ctx, monkeypatch, original, edited(original))
    saved = payloads[0]["task"]["saved_inquiry"]["premises"]
    assert audit["status"] == "model_reviewed"
    assert [item["id"] for item in saved] == ["p1"]
    assert saved[0]["statement"] == premise()["statement"]
    assert saved[0]["status"] == "unknown" and "receipts" not in saved[0]


def test_review_receives_server_arithmetic_observations_and_source_coverage(ctx, monkeypatch):
    original = params()
    evidence = {
        "calculations": [{"before": "20", "after": "10", "percent_change": "-50",
                          "descriptive_only": True, "provenance": {"kind": "user_reported"}}],
        "observations": [{"quote": "A comparison requires assumptions.",
                          "provenance": {"kind": "source_bound", "source_ref": "rec_1"}}],
        "source_coverage": {"required": ["https://example.org/rules"], "missing": []},
    }
    untouched = copy.deepcopy(evidence)
    updated, audit, payloads = review(ctx, monkeypatch, original, edited(original), evidence)
    assert evidence == untouched
    assert payloads[0]["delivery_evidence"] == evidence
    assert payloads[0]["candidate"] == original.answer
    assert updated.answer == "A conditional revised answer."
    assert audit["reviewed_answer_sha256"] == hashlib.sha256(updated.answer.encode()).hexdigest()
    assert not audit["semantic_verification"]
    assert len(payloads) == 1


def test_exact_readonly_evidence_and_claim_binding_echo_can_be_ignored(ctx, monkeypatch):
    original = params()
    evidence = {"calculations": [{"delta": "-10", "percent_change": "-50"}]}
    output = edited(original, **{
        key: original.checks[0].model_dump()[key]
        for key in ("claim_ref", "premise_ids", "task_ids", "passages")})
    output["delivery_evidence"] = copy.deepcopy(evidence)
    updated, audit, _ = review(ctx, monkeypatch, original, output, evidence)
    assert updated.answer != original.answer and audit["status"] == "model_reviewed"
    assert updated.checks[0].passages == original.checks[0].passages
    assert evidence["calculations"][0]["delta"] == "-10"


@pytest.mark.parametrize("field", ["calculations", "observations", "source_coverage"])
def test_changed_server_evidence_is_rejected_instead_of_accepting_reviewed_answer(ctx, monkeypatch, field):
    original = params()
    evidence = {"calculations": [{"percent_change": "-50"}],
                "observations": [{"quote": "The count changed from 20 to 10."}],
                "source_coverage": {"missing": ["https://example.org/coverage"]}}
    untouched = copy.deepcopy(evidence)
    output = edited(original)
    output["delivery_evidence"] = copy.deepcopy(evidence)
    output["delivery_evidence"][field] = []
    updated, audit, payloads = review(ctx, monkeypatch, original, output, evidence)
    assert updated is original and audit["status"] == "failed"
    assert "reviewed_answer_sha256" not in audit
    assert evidence == untouched and len(payloads) == 1


@pytest.mark.parametrize("mode", ["invalid_json", "invalid_binding", "skipped"])
def test_failed_or_skipped_review_cannot_reuse_a_previous_answer_digest(ctx, monkeypatch, mode):
    original = params()
    calls = []
    old_digest = hashlib.sha256(original.answer.encode()).hexdigest()

    def request(messages):
        calls.append(messages)
        audit = {"status": "model_reviewed", "reviewed_answer_sha256": old_digest,
                 "semantic_verification": False}
        if mode == "skipped":
            audit.update(status="skipped", reason="review_already_attempted")
            return None, audit
        output = edited(original, passages=[])
        return {"content": "invalid" if mode == "invalid_json" else json.dumps(output)}, audit

    monkeypatch.setattr(ctx, "request_review", request)
    updated, audit = review_candidate(ctx, original, [], delivery_evidence={})
    assert updated is original and len(calls) == 1
    assert audit["status"] == ("skipped" if mode == "skipped" else "failed")
    assert "reviewed_answer_sha256" not in audit


def test_digest_binds_normalized_reviewed_answer_and_not_the_original_draft(ctx, monkeypatch):
    original = params()
    output = edited(original)
    output["answer"] = "  已知数字仍有用；因果解释尚未确定。\n"
    updated, audit, _ = review(ctx, monkeypatch, original, output)
    assert updated.answer == output["answer"].strip()
    assert audit["reviewed_answer_sha256"] == hashlib.sha256(updated.answer.encode("utf-8")).hexdigest()
    assert audit["reviewed_answer_sha256"] != hashlib.sha256(original.answer.encode("utf-8")).hexdigest()

