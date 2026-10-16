"""Full plugin flows: decisive unknowns, honest delivery and targeted updates."""
import copy
import json

import pytest

from core import inquiry, research
from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from plugins import inquiry as plugin


RULE = "Provincial rules apply to a local retail employer. Minimum standards are not the entire employment entitlement."
UNSAFE = "The provincial rule definitely governs your employment and nothing else needs checking."


def read(ctx, ref):
    ctx._harness._execute_once(ctx.session, {"function": {"name": "record__read", "arguments": json.dumps({
        "ref": ref, "field": "text"})}}, {})


def material(ctx, text, *, prior=None, identity="employer"):
    ref = ctx.records.put(Record("document", {"text": Field(text, "extracted")}, "fixture", identity),
                          supersedes=prior)
    read(ctx, ref)
    return ref


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    h = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    question = "Which employment rules apply to me, and do minimum standards exhaust my rights?"
    session.messages.append({"role": "user", "content": question})
    session.timeline.append({"role": "user", "text": question})
    context = h.context(session, "inquiry")
    material(context, RULE, identity="rules")
    material(context, "The employer operates only a local retail shop.")
    monkeypatch.setattr(plugin, "review_candidate", lambda ctx, p, contexts, delivery_evidence=None: (
        p, {"status": "skipped", "semantic_verification": False}))
    return context


def premise_spec(**updates):
    values = {"statement": "The employer operates only a local retail shop.",
              "if_true": "The provincial rule is relevant, subject to the other applicability conditions.",
              "if_false": "Check the employer's industry and the potentially applicable federal rules.",
              "next_check": "Identify the employer's actual business and territorial operations.",
              "method": "source", "status": "unknown"}
    values.update(updates)
    return values


def check(*, direct=False, **updates):
    if direct:
        values = {"conclusion": "Minimum standards do not exhaust the employment entitlement.",
                  "passages": [{"source_ref": "rec_1", "quote": "Minimum standards are not the entire employment entitlement."}],
                  "reasoning": "This is the narrow statement made by the retrieved text.",
                  "objection": "A particular additional entitlement may not apply.",
                  "response": "No particular additional entitlement is determined here.",
                  "claim_kind": "direct", "verdict": "supported"}
    else:
        values = {"conclusion": "The provincial rule applies to this employer.",
                  "passages": [{"source_ref": "rec_1", "quote": "Provincial rules apply to a local retail employer."}],
                  "reasoning": "The rule applies only if the employer meets its factual condition.",
                  "objection": "The actual industry may put the employment under another regime.",
                  "response": "The source establishes the condition; the employer facts must be checked separately.",
                  "claim_kind": "inference", "verdict": "supported", "premises": [premise_spec()]}
    values.update(updates)
    return values


def conclude(ctx, *, checks=None, **updates):
    values = {"calculations": [], "answer": UNSAFE, "checks": checks or [check()]}
    values.update(updates)
    return plugin.conclude(ctx, plugin.ConcludeParams.model_validate(values))


def inspect(ctx, ref=""):
    return plugin.inspect_object(ctx, plugin.InspectParams(ref=ref)).content


def test_inline_unknown_keeps_effective_verdict_limited_and_replaces_categorical_delivery(ctx):
    result = conclude(ctx)
    report = inspect(ctx, result.content["ref"])
    assessment = report["assessments"][0]
    assert assessment["verdict"] == "supported"
    assert assessment["effective_verdict"] == "limited"
    assert report["premises"][0]["effective_status"] == "unknown"
    assert result.reply != UNSAFE and UNSAFE not in result.reply
    assert premise_spec()["if_true"] not in result.reply
    assert premise_spec()["if_false"] not in result.reply
    assert premise_spec()["statement"] in result.reply
    assert "The source states (rec_1)" in result.reply
    assert premise_spec()["next_check"] in result.reply
    assert report["object"]["answer"]["text"] == result.reply
    assert report["object"]["answer"]["review"]["conditional_gate"]


def test_answer_tool_cannot_restore_the_categorical_draft_over_unknown_premises(ctx):
    completed = conclude(ctx)
    result = plugin.answer(ctx, plugin.AnswerParams(ref=completed.content["ref"], text=UNSAFE,
                                                  claim_refs=completed.content["claim_refs"]))
    assert result.reply != UNSAFE and UNSAFE not in result.reply
    assert premise_spec()["next_check"] in result.reply
    assert inspect(ctx)["object"]["answer"]["text"] == result.reply


def test_answer_tool_cannot_omit_all_claim_refs_to_bypass_recorded_unknowns(ctx):
    completed = conclude(ctx)
    before = len(ctx.records.all())
    try:
        result = plugin.answer(ctx, plugin.AnswerParams(ref=completed.content["ref"], text=UNSAFE, claim_refs=[]))
    except ValueError:
        assert len(ctx.records.all()) == before
    else:
        assert result.reply != UNSAFE and UNSAFE not in result.reply


def test_update_reopens_only_the_linked_argument_and_answer_cannot_bypass_recheck(ctx):
    completed = conclude(ctx, checks=[check(), check(direct=True)])
    initial = inspect(ctx)
    linked, unrelated = [row["claim_ref"] for row in initial["assessments"]]
    changed = plugin.premise(ctx, plugin.PremiseParams(ref=initial["ref"], **premise_spec(
        id="p1", status="supported", evidence_refs=["rec_2"])))
    report = changed.content
    by_ref = {row["claim_ref"]: row for row in report["assessments"]}
    assert report["affected_claim_refs"] == [linked]
    assert by_ref[linked]["needs_recheck"] and not by_ref[unrelated]["needs_recheck"]
    assert report["answer_needs_refresh"]
    attempt = plugin.answer(ctx, plugin.AnswerParams(ref=report["ref"], text=UNSAFE, claim_refs=[linked, unrelated]))
    assert UNSAFE not in attempt.reply
    assert by_ref[unrelated]["effective_verdict"] == initial["assessments"][1]["effective_verdict"]
    assert check(direct=True)["conclusion"] not in attempt.reply
    assert "answer review did not complete" in attempt.reply
    assert inspect(ctx)["assessments"][0]["needs_recheck"]


def test_source_replacement_reopens_only_claims_using_that_premise(ctx):
    known = premise_spec(status="supported", evidence_refs=["rec_2"])
    completed = conclude(ctx, checks=[check(premises=[known]), check(direct=True)], answer="The rule applies on the supplied employer facts.")
    initial = inspect(ctx)
    linked, unrelated = [row["claim_ref"] for row in initial["assessments"]]
    replacement = material(ctx, "The employer operates an interprovincial transport business.", prior="rec_2")
    report = inspect(ctx)
    assert report["premises"][0]["effective_status"] == "unknown"
    assert report["premises"][0]["evidence_refs"] == ["rec_2"]
    assert report["affected_claim_refs"] == [linked] and report["answer_needs_refresh"]
    assert not next(row for row in report["assessments"] if row["claim_ref"] == unrelated)["needs_recheck"]
    assert replacement in report["pending_materials"]
    assert report["object"]["answer"]["text"] == completed.reply


def test_independent_new_material_is_pending_without_rewriting_existing_conclusions(ctx):
    completed = conclude(ctx, checks=[check(direct=True)], answer="Minimum standards alone do not establish the entire entitlement.")
    initial = inspect(ctx)
    other = material(ctx, "The weather was rainy that day.", identity="unrelated")
    report = inspect(ctx)
    assert other in report["pending_materials"]
    assert report["affected_claim_refs"] == [] and not report["answer_needs_refresh"]
    assert report["assessments"] == initial["assessments"]
    assert report["object"]["answer"]["text"] == completed.reply


@pytest.mark.parametrize("invalid", ["fabricated_user_quote", "missing_source_receipt", "unknown_premise_id", "late_invalid_premise"])
def test_inline_validation_is_atomic_even_after_an_earlier_valid_check(ctx, invalid):
    specs = [check(direct=True), check()]
    if invalid == "fabricated_user_quote":
        specs[1]["premises"] = [premise_spec(method="user", status="supported", user_quote="I work in a local retail shop.")]
    elif invalid == "missing_source_receipt":
        unread = ctx.records.put(Record("document", {"text": Field("A never-read source.", "extracted")}, "fixture", "unread"))
        specs[1]["premises"] = [premise_spec(status="supported", evidence_refs=[unread])]
    elif invalid == "unknown_premise_id":
        specs[1].update(premises=[], premise_ids=["not-saved"])
    else:
        specs[1]["premises"] = [premise_spec(), premise_spec(method="user", status="supported", user_quote="Invented user words")]
    before = list(ctx.records.all())
    state = copy.deepcopy(ctx.session.state)
    with pytest.raises(ValueError):
        conclude(ctx, checks=specs)
    assert list(ctx.records.all()) == before
    assert ctx.session.state.get("inquiry", {}) == state.get("inquiry", {})


def test_recheck_claim_ref_replaces_candidate_and_removes_obsolete_assessment(ctx):
    completed = conclude(ctx, checks=[check(), check(direct=True)])
    original = inspect(ctx)
    linked, unrelated = [row["claim_ref"] for row in original["assessments"]]
    changed = plugin.premise(ctx, plugin.PremiseParams(ref=original["ref"], **premise_spec(
        id="p1", status="supported", evidence_refs=["rec_2"])))
    revised = conclude(ctx, ref=changed.content["ref"], answer="The provincial rule applies on the current employer facts.",
                       checks=[check(claim_ref=linked, premises=[], premise_ids=["p1"])])
    report = inspect(ctx)
    new_claim = revised.content["claim_refs"][0]
    assert new_claim != linked and ctx.records.current_ref(linked) == new_claim
    assert set(report["object"]["candidate_refs"]) == {new_claim, unrelated}
    assert linked not in {row["claim_ref"] for row in report["assessments"]}
    assert not report["affected_claim_refs"] and not report["answer_needs_refresh"]
    assert not report["stale"]
    renewed = next(row for row in report["assessments"] if row["claim_ref"] == new_claim)
    assert renewed["effective_verdict"] == "supported" and not renewed["needs_recheck"]
    assert inquiry.unpack(ctx.records.get_exact(completed.content["ref"]))["premises"][0]["status"] == "unknown"


def test_user_quote_is_auto_bound_to_real_user_text_without_claiming_source_verification(ctx):
    text = "I work in a local retail shop."
    ctx.session.messages.append({"role": "user", "content": text})
    ctx.session.timeline.append({"role": "user", "text": text})
    result = plugin.premise(ctx, plugin.PremiseParams(**premise_spec(
        method="user", status="supported", user_quote="local retail shop")))
    value = result.content["premises"][0]
    assert value["basis"] == "user_reported"
    assert value["effective_status"] == "supported"
    assert not value["semantic_verification"] and not value["legal_verification"]


def test_hypothesis_remains_conditional_with_a_supported_premise(ctx):
    result = conclude(ctx, checks=[check(claim_kind="hypothesis", premises=[premise_spec(
        status="supported", evidence_refs=["rec_2"])])])
    assert UNSAFE not in result.reply
    assert inspect(ctx)["assessments"][0]["effective_verdict"] == "limited"


def test_conditional_fallback_preserves_bound_observations_without_releasing_free_form_facts(ctx):
    facts = ["The employer facts establish that every provincial rule applies without exception."]
    observations = [{"source_ref": "rec_1", "quote": "Minimum standards are not the entire employment entitlement."},
                    {"source_ref": "rec_2", "quote": "The employer operates only a local retail shop."}]
    result = conclude(ctx, facts=facts, observations=observations)
    assert UNSAFE not in result.reply
    for fact in facts:
        assert fact not in result.reply
    for observation in observations:
        assert observation["quote"] in result.reply
    assert "The source states" in result.reply
    assert premise_spec()["next_check"] in result.reply
    assert inspect(ctx)["object"]["answer"]["text"] == result.reply
    assert inspect(ctx)["assessments"][0]["effective_verdict"] == "limited"


def test_direct_label_cannot_release_an_unreviewed_inference_or_free_form_facts(ctx):
    result = conclude(ctx, checks=[check(direct=True, conclusion=UNSAFE)], facts=[UNSAFE])
    assert UNSAFE not in result.reply
    assert "The source states (rec_1)" in result.reply
    assert "Minimum standards are not the entire employment entitlement." in result.reply
    assert inspect(ctx)["object"]["answer"]["text"] == result.reply


def test_successful_review_keeps_a_useful_conditional_answer_instead_of_replacing_it_with_branches(ctx, monkeypatch):
    from plugins.inquiry.review import review_candidate

    answer = ("Minimum standards do not exhaust employment rights. If the employer meets the local-retail "
              "condition, the provincial rule is relevant; its actual operations remain to be checked.")
    original = plugin.ConcludeParams.model_validate({"calculations": [], "answer": UNSAFE, "checks": [check()]})
    response = {"answer": answer, "checks": [reviewed_check(original.checks[0], verdict="limited")],
                "corrections": ["Retained the general rule while qualifying personal applicability."]}
    monkeypatch.setattr(ctx, "request_review", lambda messages: (
        {"content": json.dumps(response)}, {"status": "model_reviewed", "semantic_verification": False}))
    monkeypatch.setattr(plugin, "review_candidate", review_candidate)
    result = plugin.conclude(ctx, original)
    report = inspect(ctx)
    assert result.reply == answer and UNSAFE not in result.reply
    assert report["premises"][0]["effective_status"] == "unknown"
    assert report["assessments"][0]["effective_verdict"] == "limited"
    assert report["object"]["answer"]["review"]["reviewed_answer_sha256"] == research.digest(answer)
    assert report["object"]["answer"]["gaps"]


def reviewed_check(original, **updates):
    fields = {key: getattr(original, key) for key in ("conclusion", "reasoning", "objection", "response", "verdict")}
    fields.update(updates)
    return fields


@pytest.mark.parametrize("status", ["unknown", "supported"])
def test_review_can_correct_branches_but_preserves_premise_identity_status_and_evidence(ctx, monkeypatch, status):
    from plugins.inquiry.review import review_candidate

    original = plugin.ConcludeParams.model_validate({"calculations": [], "answer": UNSAFE, "checks": [check(premises=[premise_spec(
        id="p1", status=status, evidence_refs=["rec_2"],
        if_true="All entitlement questions are settled.", if_false="There are no employment rights.")])]})
    before = original.model_dump()
    branches = {"index": 0, "if_true": "The provincial rule is relevant, subject to its other conditions.",
                "if_false": "Determine the applicable regime; this does not establish an absence of rights."}
    response = {"answer": "The applicable regime remains conditional on the employer facts.",
                "checks": [reviewed_check(original.checks[0], premise_branches=[branches])],
                "corrections": ["Removed unjustified consequences from both premise branches."]}
    monkeypatch.setattr(ctx, "request_review", lambda messages: (
        {"content": json.dumps(response)}, {"status": "model_reviewed", "semantic_verification": False}))
    updated, audit = review_candidate(ctx, original, [{"text": RULE}])
    old_premise = original.checks[0].premises[0].model_dump()
    new_premise = updated.checks[0].premises[0].model_dump()
    assert new_premise["if_true"] == branches["if_true"] and new_premise["if_false"] == branches["if_false"]
    for key in set(old_premise) - {"if_true", "if_false"}:
        assert new_premise[key] == old_premise[key]
    assert new_premise["status"] == status and new_premise["evidence_refs"] == ["rec_2"]
    assert updated.checks[0].passages == original.checks[0].passages
    assert original.model_dump() == before and audit["status"] == "model_reviewed"


@pytest.mark.parametrize("forged", [{"status": "supported"}, {"evidence_refs": ["rec_999"]}, {"statement": "A replacement proposition"}])
def test_branch_review_cannot_smuggle_status_or_evidence_changes(ctx, monkeypatch, forged):
    from plugins.inquiry.review import review_candidate

    original = plugin.ConcludeParams.model_validate({"calculations": [], "answer": UNSAFE, "checks": [check()]})
    changed = {"index": 0, "if_true": "A revised consequence.", "if_false": "Another revised consequence.", **forged}
    response = {"answer": "A replacement answer.", "checks": [reviewed_check(original.checks[0], premise_branches=[changed])],
                "corrections": []}
    monkeypatch.setattr(ctx, "request_review", lambda messages: (
        {"content": json.dumps(response)}, {"status": "model_reviewed", "semantic_verification": False}))
    updated, audit = review_candidate(ctx, original, [])
    assert updated is original and audit["status"] == "failed"
    assert updated.checks[0].premises[0].status == "unknown"
    assert updated.checks[0].premises[0].evidence_refs == []


def test_review_corrects_facts_conditions_and_gaps_against_user_givens_before_conditional_delivery(ctx, monkeypatch):
    from plugins.inquiry.review import review_candidate

    question = "A policy was introduced before complaints fell from 20 to 10; a comparison group fell from 20 to 5. Did the policy cause the decline?"
    ctx.session.messages[-1]["content"] = question
    ctx.session.timeline[-1]["text"] = question
    source = material(ctx, "Observed association alone does not establish causation.", identity="methods")
    condition = premise_spec(statement="The groups would have followed comparable trends without the policy.",
                             if_true="The comparison identifies the policy effect without further conditions.",
                             if_false="The policy had no effect.", next_check="Compare pre-policy trends and measurement definitions.")
    check_values = check(conclusion="The policy caused the decline.", premises=[condition], passages=[{
        "source_ref": source, "quote": "Observed association alone does not establish causation."}])
    original = plugin.ConcludeParams.model_validate({"calculations": [], "answer": "The policy definitely caused the decline.", "checks": [check_values],
        "facts": ["The user says the sample size is 20."], "conditions": ["The sample size is 20, as supplied by the user."],
        "gaps": ["It is unknown whether policy introduction preceded the decline."],
        "calculations": [
            {"operation": "percent_change", "before": 20, "after": 10,
             "quote": "complaints fell from 20 to 10"},
            {"operation": "percent_change", "before": 20, "after": 5,
             "quote": "a comparison group fell from 20 to 5"}]})
    facts = ["The supplied complaint counts fell by 50% in the policy group and 75% in the comparison group.",
             "Those changes alone do not establish a policy effect."]
    conditions = ["The supplied numbers are complaint counts; no population denominator is given."]
    gaps = ["Comparable measurement windows and the identifying assumptions remain unchecked."]
    branches = {"index": 0, "if_true": "If the other identifying assumptions hold, the comparison may inform an effect estimate.",
                "if_false": "This comparison cannot identify the effect; an effect may still exist."}
    response = {"answer": "The counts show declines, but the causal effect remains unestablished.", "facts": facts,
                "conditions": conditions, "gaps": gaps, "checks": [reviewed_check(original.checks[0],
                    conclusion="The descriptive declines do not establish the policy's causal effect.",
                    verdict="limited", premise_branches=[branches])],
                "corrections": ["Preserved the stated temporal order; removed the invented sample-size claim."]}
    requests = []

    def review_request(messages):
        requests.append(json.loads(messages[1]["content"]))
        return {"content": json.dumps(response)}, {"status": "model_reviewed", "semantic_verification": False}

    monkeypatch.setattr(ctx, "request_review", review_request)
    monkeypatch.setattr(plugin, "review_candidate", review_candidate)
    result = plugin.conclude(ctx, original)
    saved = inspect(ctx)["object"]["answer"]
    assert len(requests) == 1 and requests[0]["task"]["latest_user_message"] == question
    assert requests[0]["conditions"] == original.conditions and requests[0]["facts"] == original.facts
    assert response["answer"] in result.reply
    assert "-50%" in result.reply and "-75%" in result.reply
    assert "(10 − 20) / 20 × 100%" in result.reply
    assert "(5 − 20) / 20 × 100%" in result.reply
    assert "sample size is 20" not in result.reply
    assert branches["if_true"] not in result.reply and branches["if_false"] not in result.reply
    assert inspect(ctx)["premises"][0]["if_true"] == branches["if_true"]
    assert [item["percent_change"] for item in requests[0]["delivery_evidence"]["calculations"]] == ["-50", "-75"]
    assert [item["percent_change"] for item in saved["delivery_evidence"]["calculations"]] == ["-50", "-75"]
    assert saved["conditions"] == conditions
    assert gaps[0] in saved["gaps"] and original.gaps[0] not in saved["gaps"]
    assert inspect(ctx)["premises"][0]["status"] == "unknown"


def test_missing_decisive_premise_is_repaired_before_consuming_the_single_review(ctx, monkeypatch):
    calls = []

    def review(ctx, params, contexts, delivery_evidence=None):
        calls.append(params)
        assert len(calls) == 1, "An argument repair must not consume the final review allowance."
        revised = params.model_copy(update={"answer": "Applicability remains conditional on the employer's actual operations."})
        return revised, {"status": "model_reviewed", "semantic_verification": False,
                         "reviewed_answer_sha256": research.digest(revised.answer)}

    monkeypatch.setattr(plugin, "review_candidate", review)
    before = list(ctx.records.all())
    with pytest.raises(ValueError, match="decisive premise"):
        conclude(ctx, checks=[check(premises=[])])
    assert calls == [] and list(ctx.records.all()) == before
    repaired = conclude(ctx)
    assert len(calls) == 1 and repaired.content["answer_delivered"]
    assert inspect(ctx)["object"]["answer"]["review"]["status"] == "model_reviewed"


@pytest.mark.parametrize("changed_field", [None, "premise_ids", "task_ids", "passages"])
def test_review_accepts_exact_readonly_echo_but_rejects_changed_bindings(ctx, monkeypatch, changed_field):
    from plugins.inquiry.review import review_candidate

    original = plugin.ConcludeParams.model_validate({"calculations": [], "answer": UNSAFE, "checks": [check(
        premise_ids=["p1"], premises=[], task_ids=["q1"])]})
    binding = original.checks[0].model_dump(include={"premise_ids", "task_ids", "passages"})
    if changed_field == "passages":
        binding["passages"][0]["source_ref"] = "rec_2"
    elif changed_field is not None:
        binding[changed_field] = ["different-binding"]
    response = {"answer": "Applicability remains conditional on the employer facts.",
                "checks": [reviewed_check(original.checks[0], **binding,
                    conclusion="Employer facts are needed to establish applicability.", verdict="limited")],
                "corrections": ["Removed the unconditional applicability assertion."]}
    monkeypatch.setattr(ctx, "request_review", lambda messages: (
        {"content": json.dumps(response)}, {"status": "model_reviewed", "semantic_verification": False}))
    updated, audit = review_candidate(ctx, original, [{"text": RULE}])
    if changed_field is None:
        assert audit["status"] == "model_reviewed" and updated.answer == response["answer"]
        assert updated.checks[0].conclusion == response["checks"][0]["conclusion"]
    else:
        assert audit["status"] == "failed" and updated is original
    assert updated.checks[0].premise_ids == original.checks[0].premise_ids
    assert updated.checks[0].task_ids == original.checks[0].task_ids
    assert updated.checks[0].passages == original.checks[0].passages
