"""Completion delivers bound observations/math and honours unfinished source work."""
import hashlib
import json
import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core import inquiry
from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from harness.receipts import issue
from harness.runtime import TurnBudget
from plugins import inquiry as plugin


QUESTION = "Before policy: 120 cases; after policy: 100 cases. Did the policy cause the decline?"
QUOTE = "Before policy: 120 cases; after policy: 100 cases."
SOURCE_QUOTE = "A descriptive comparison does not establish a causal effect."
URL = "https://example.org/decisive-coverage"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    harness = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    harness.config["enabled"] = list(dict.fromkeys([*harness.config["enabled"], "web"]))
    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": QUESTION})
    session.timeline.append({"role": "user", "text": QUESTION})
    ref = session.records.put(Record("document", {"text": Field(SOURCE_QUOTE, "extracted")}, "fixture", "comparison"))
    harness._execute_once(session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": ref, "field": "text"})}}, {})
    monkeypatch.setattr(plugin, "review_candidate", lambda ctx, params, contexts, delivery_evidence=None: (
        params, {"status": "skipped", "semantic_verification": False}))
    return harness.context(session, "inquiry")


def params(**changes):
    values = {"answer": "UNSUPPORTED_DRAFT: The policy definitely caused the decline.",
              "facts": ["UNSUPPORTED_FACT: The common cause is already proven."],
              "observations": [{"quote": QUOTE}],
              "calculations": [{"operation": "percent_change", "before": 120, "after": 100, "quote": QUOTE}],
              "checks": [{"claim_kind": "direct", "conclusion": "UNSUPPORTED_DIRECT: The policy is certainly responsible.",
                          "passages": [{"source_ref": "rec_1", "quote": SOURCE_QUOTE}],
                          "reasoning": "This records the evidence, but its interpretation still needs review.",
                          "objection": "Other causes and comparability are unknown.",
                          "response": "No identifying assumptions have yet been established.", "verdict": "supported"}]}
    values.update(changes)
    return plugin.ConcludeParams.model_validate(values)


def saved(ctx, result):
    return inquiry.inspect(ctx.records, result.content["ref"], ctx.session.tool_receipts)


def read_source(ctx, text, url=""):
    fields = {"text": Field(text, "extracted")}
    if url:
        fields["url"] = Field(url, "extracted")
    ref = ctx.records.put(Record("document", fields, "fixture", url or "additional"))
    ctx._harness._execute_once(ctx.session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": ref, "field": "text"})}}, {})
    return ref


def append_user(ctx, text):
    ctx.session.messages.append({"role": "user", "content": text})
    ctx.session.timeline.append({"role": "user", "text": text})


def reviewed(monkeypatch, *, status="model_reviewed", bad_hash=False):
    def review(ctx, proposal, contexts, delivery_evidence=None):
        proposal = proposal.model_copy(deep=True)
        proposal.answer = "REVIEWED_ANSWER: The observed decline alone does not identify the policy's causal effect."
        return proposal, {"status": status,
                          "reviewed_answer_sha256": "wrong" if bad_hash else digest(proposal.answer),
                          "semantic_verification": False, "corrections": ["Removed an unsupported causal assertion."]}
    monkeypatch.setattr(plugin, "review_candidate", review)


@pytest.mark.parametrize("review_status", ["skipped", "failed"])
def test_without_completed_review_only_bound_observations_and_math_survive(ctx, monkeypatch, review_status):
    monkeypatch.setattr(plugin, "review_candidate", lambda ctx, p, contexts, delivery_evidence=None: (
        p, {"status": review_status, "semantic_verification": False}))
    result = plugin.conclude(ctx, params())
    assert result.final and result.content["answer_delivered"]
    assert QUOTE in result.reply and "You provided" in result.reply
    assert "120" in result.reply and "100" in result.reply and "-16.6667%" in result.reply
    for unsafe in ("UNSUPPORTED_DRAFT", "UNSUPPORTED_DIRECT", "UNSUPPORTED_FACT"):
        assert unsafe not in result.reply
    answer = saved(ctx, result)["object"]["answer"]
    assert answer["text"] == result.reply
    evidence = answer["delivery_evidence"]
    assert evidence["observations"][0]["provenance"]["quote"] == QUOTE
    assert evidence["calculations"][0]["delta"] == "-20"
    assert evidence["calculations"][0]["descriptive_only"]
    assert evidence["source_checks"] == []


def test_unreviewed_premise_branches_do_not_smuggle_new_certainty_into_the_answer(ctx):
    p = params()
    p.checks[0].claim_kind = "inference"
    p.checks[0].premises = [plugin.PremiseSpec(
        statement="Comparable populations are available.",
        if_true="UNSAFE_TRUE_BRANCH: the policy effect is certainly proven.",
        if_false="UNSAFE_FALSE_BRANCH: a common cause definitely explains everything.",
        next_check="Check the population counts and measurement definitions for both periods.")]
    result = plugin.conclude(ctx, p)
    assert "Check the population counts" in result.reply
    assert "UNSAFE_TRUE_BRANCH" not in result.reply and "UNSAFE_FALSE_BRANCH" not in result.reply
    assert "UNSUPPORTED" not in result.reply


@pytest.mark.parametrize("status,bad_hash", [("model_reviewed", True), ("skipped", False), ("failed", False)])
def test_review_audit_requires_both_success_and_the_exact_delivered_draft_hash(ctx, monkeypatch, status, bad_hash):
    reviewed(monkeypatch, status=status, bad_hash=bad_hash)
    result = plugin.conclude(ctx, params())
    assert "REVIEWED_ANSWER" not in result.reply
    assert "-16.6667%" in result.reply


def test_successful_review_keeps_useful_answer_and_cannot_drop_the_computed_result(ctx, monkeypatch):
    received = []
    def review(ctx, proposal, contexts, delivery_evidence=None):
        received.append(delivery_evidence)
        replacement = proposal.model_copy(update={"answer": "REVIEWED_ANSWER: These counts alone do not establish causation."})
        return replacement, {"status": "model_reviewed", "reviewed_answer_sha256": digest(replacement.answer),
                             "semantic_verification": False}
    monkeypatch.setattr(plugin, "review_candidate", review)
    result = plugin.conclude(ctx, params())
    assert result.reply.startswith("REVIEWED_ANSWER")
    assert "(100 − 120) / 120 × 100% = ≈-16.6667%" in result.reply
    assert received[0]["calculations"][0]["delta"] == "-20"
    assert saved(ctx, result)["object"]["answer"]["delivery_evidence"]["calculations"][0]["delta"] == "-20"


def test_new_source_version_marks_an_answer_using_only_that_calculation_stale(ctx, monkeypatch):
    reviewed(monkeypatch)
    ref = read_source(ctx, "The count was 200 before and 160 after.")
    p = params(calculations=[{"operation": "percent_change", "before": 200, "after": 160,
                            "quote": "The count was 200 before and 160 after.", "source_ref": ref}])
    result = plugin.conclude(ctx, p)
    assert not saved(ctx, result)["answer_needs_refresh"]
    ctx.records.put(Record("document", {"text": Field("The corrected count was 200 before and 180 after.", "extracted")},
                           "fixture", "additional"), supersedes=ref)
    assert saved(ctx, result)["answer_needs_refresh"]


@pytest.mark.parametrize("bad", ["observation", "calculation"])
def test_invalid_delivery_evidence_fails_before_any_compound_state_is_saved(ctx, bad):
    p = params()
    if bad == "observation":
        p.observations[0].quote = "An invented user fact."
    else:
        p.calculations[0].after = Decimal(99)
    before = ctx.records.entries()
    with pytest.raises(ValueError):
        plugin.conclude(ctx, p)
    assert ctx.records.entries() == before


def test_user_named_decisive_source_is_requested_once_before_completion(ctx):
    append_user(ctx, "Use this decisive source before answering: " + URL)
    before = ctx.records.entries()
    first = plugin.conclude(ctx, params())
    assert first.content["status"] == "needs_sources" and not first.final
    assert not first.reply and URL in json.dumps(first.content)
    assert ctx.records.entries() == before
    # An ignored request cannot trap completion in an unbounded defer loop.
    second = plugin.conclude(ctx, params())
    assert second.final and second.reply
    answer = saved(ctx, second)["object"]["answer"]
    assert answer["delivery_evidence"]["source_checks"][0]["status"] == "unvisited"
    assert any(URL in gap for gap in answer["gaps"])
    assert URL in second.reply and "UNSUPPORTED_DRAFT" not in second.reply


def test_reading_the_requested_source_then_allows_complete_delivery(ctx, monkeypatch):
    append_user(ctx, "Please check " + URL)
    first = plugin.conclude(ctx, params())
    assert first.content["status"] == "needs_sources"
    ref = read_source(ctx, "The decisive qualification limits which population the rule covers.", URL)
    reviewed(monkeypatch)
    result = plugin.conclude(ctx, params())
    assert result.final and result.reply.startswith("REVIEWED_ANSWER")
    check = saved(ctx, result)["object"]["answer"]["delivery_evidence"]["source_checks"][0]
    assert check["status"] == "retrieved" and check["refs"] == [ref] and check["receipt_ids"]


def test_low_remaining_budget_delivers_a_clear_gap_instead_of_a_new_defer(ctx):
    append_user(ctx, "Use " + URL)
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 2, 18, 60, 0)
    result = plugin.conclude(ctx, params())
    assert result.final and result.reply
    answer = saved(ctx, result)["object"]["answer"]
    assert any(URL in gap for gap in answer["gaps"])
    assert answer["delivery_evidence"]["source_checks"][0]["status"] == "unvisited"
    assert "UNSUPPORTED_DRAFT" not in result.reply


def test_failed_source_attempt_is_preserved_as_failure_and_does_not_defer_again(ctx):
    append_user(ctx, "Use " + URL)
    receipt = issue(ctx.session, ctx._harness.plugins, "web__fetch", {"error": "The page is blocked.", "kind": "tool_value_error"})
    ctx.session.messages.extend([
        {"role": "assistant", "tool_calls": [{"id": "failed_source", "function": {"name": "web__fetch", "arguments": json.dumps({"url": URL})}}]},
        {"role": "tool", "tool_call_id": "failed_source", "content": json.dumps({"error": "The page is blocked.", "_execution_receipt": {"id": receipt["id"]}})},
    ])
    result = plugin.conclude(ctx, params())
    assert result.final and URL in result.reply
    answer = saved(ctx, result)["object"]["answer"]
    row = answer["delivery_evidence"]["source_checks"][0]
    assert row["status"] == "failed" and row["receipt_ids"] == [receipt["id"]]
    assert any(URL in gap for gap in answer["gaps"])


def test_completion_without_calculations_still_saves_the_bound_answer(ctx):
    payload = params().model_dump()
    del payload["calculations"]
    result = plugin.conclude(ctx, plugin.ConcludeParams.model_validate(payload))
    answer = saved(ctx, result)["object"]["answer"]
    assert result.final and answer["text"] == result.reply
    assert answer["delivery_evidence"]["calculations"] == []
    assert QUOTE in result.reply


def user_premise(**changes):
    value = {"statement": "The user reports the supplied event counts.", "if_true": "Use those counts only for a descriptive comparison.",
             "if_false": "Obtain corrected counts before comparing.", "next_check": "Ask the user to confirm the reported counts.",
             "status": "supported", "user_quote": QUOTE}
    value.update(changes)
    return plugin.PremiseSpec.model_validate(value)


def test_omitted_premise_method_binds_exact_user_wording_without_promoting_it_to_a_source(ctx):
    premise = user_premise()
    assert premise.method == "user"
    p = params()
    p.checks[0].premises = [premise]
    result = plugin.conclude(ctx, p)
    bound = saved(ctx, result)["premises"][0]
    assert bound["method"] == "user" and bound["basis"] == "user_reported"
    assert bound["user_quote"] == QUOTE and bound["user_binding"]["quote_sha256"] == digest(QUOTE)
    assert not bound["semantic_verification"]


@pytest.mark.parametrize("changes", [{"user_quote": "Invented user wording."}, {"method": "source"}])
def test_inferred_user_method_still_rejects_fabricated_quotes_and_explicit_source_conflicts(ctx, changes):
    p = params()
    p.checks[0].premises = [user_premise(**changes)]
    before = ctx.records.entries()
    with pytest.raises(ValueError):
        plugin.conclude(ctx, p)
    assert ctx.records.entries() == before


def long_source_with_earlier_exception_search(ctx):
    exception = "Exception: temporary project staff are excluded from the general rule."
    general = "General rule: eligible permanent employees receive notice."
    text = "Scope overview. " + "a" * 6000 + "\n" + exception + "\n" + "b" * 9000 + "\n" + general + "\n" + "c" * 6000
    ref = ctx.records.put(Record("document", {"text": Field(text, "extracted")}, "fixture", "long-rule"))
    def execute(name, arguments):
        return ctx._harness._execute_once(ctx.session, {"function": {"name": name, "arguments": json.dumps(arguments)}}, {})
    execute("record__search", {"ref": ref, "field": "text", "query": "temporary project staff"})
    # The last four reads omit the early exception. Review must retain the
    # earlier successful search instead of selecting only recent read spans.
    late = text.index(general)
    for index in range(5):
        execute("record__read", {"ref": ref, "field": "text", "offset": late + index * 350, "limit": 300})
    return ref, exception, general


def test_final_completion_context_keeps_an_earlier_searched_exception_after_later_reads(ctx):
    ref, exception, _ = long_source_with_earlier_exception_search(ctx)
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    before = list(ctx.session.tool_receipts)
    request = plugin.prepare_completion(ctx, "The general rule applies to all staff without exception. " * 3)
    assert request is not None
    payload = json.loads(request.messages[1]["content"])
    contexts = [row for row in payload["source_contexts"] if row.get("source_ref") == ref]
    assert any(exception in row["text"] for row in contexts)
    assert any(row["text"].startswith("Scope overview.") for row in contexts)
    assert ctx.session.tool_receipts == before


def test_normal_review_receives_earlier_exception_even_when_check_quotes_only_the_late_rule(ctx, monkeypatch):
    ref, exception, general = long_source_with_earlier_exception_search(ctx)
    captured = []
    def review(ctx, proposal, contexts, delivery_evidence=None):
        captured.extend(contexts)
        return proposal, {"status": "skipped", "semantic_verification": False}
    monkeypatch.setattr(plugin, "review_candidate", review)
    p = params(calculations=[], observations=[])
    p.checks[0].passages = [plugin.Passage(source_ref=ref, quote=general)]
    result = plugin.conclude(ctx, p)
    assert result.final
    assert any(row.get("ref") == ref and exception in row["text"] for row in captured)
    assert any(row.get("ref") == ref and row["text"].startswith("Scope overview.") for row in captured)


def fail_after_valid_calculation(ctx, p=None):
    p = p or params()
    p.checks[0].passages[0].source_ref = "user"
    before = ctx.records.entries()
    with pytest.raises(plugin.PassageSelectionError) as failure:
        plugin.conclude(ctx, p)
    assert ctx.records.entries() == before
    assert "rec_1" in failure.value.repair["available_source_refs"]
    assert "observations/calculations" in str(failure.value)


def completion_after_failed_claim(ctx):
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    request = plugin.prepare_completion(ctx, "An unfinished interpretation requiring review. " * 4)
    assert request is not None
    return request, json.loads(request.messages[1]["content"])


def test_bad_user_source_ref_leaves_no_half_artifact_but_preserves_verified_math_for_fallback(ctx):
    fail_after_valid_calculation(ctx)
    request, payload = completion_after_failed_claim(ctx)
    assert payload["validated_calculations"][0]["delta"] == "-20"
    assert payload["calculation_specs"][0]["quote"] == QUOTE
    assert "(100 − 120) / 120 × 100% = ≈-16.6667%" in request.failure_reply
    assert "UNSUPPORTED_DRAFT" not in request.failure_reply


def test_a_new_user_turn_does_not_inherit_pending_calculations(ctx):
    fail_after_valid_calculation(ctx)
    append_user(ctx, "Now consider an unrelated matter without a numeric comparison.")
    request, payload = completion_after_failed_claim(ctx)
    assert payload["validated_calculations"] == [] and payload["calculation_specs"] == []
    assert "-16.6667%" not in request.failure_reply


@pytest.mark.parametrize("change", ["source_version", "quote", "receipt"])
def test_cached_source_calculations_are_revalidated_before_fallback(ctx, change):
    quote = "The count was 200 before and 160 after."
    ref = read_source(ctx, quote)
    p = params(calculations=[{"operation": "percent_change", "before": 200, "after": 160,
                             "quote": quote, "source_ref": ref}])
    fail_after_valid_calculation(ctx, p)
    if change == "source_version":
        ctx.records.put(Record("document", {"text": Field("The corrected count was 200 before and 180 after.", "extracted")},
                               "fixture", "additional"), supersedes=ref)
    elif change == "quote":
        ctx.state["pending_calculations"]["specs"][0]["quote"] = "Invented figures: 200 before and 160 after."
    else:
        receipt = next(row for row in ctx.session.tool_receipts if any(item["ref"] == ref for item in row["refs"]))
        receipt["refs"][0]["fingerprint"] = "tampered"
    request, payload = completion_after_failed_claim(ctx)
    assert payload["validated_calculations"] == [] and payload["calculation_specs"] == []
    assert "(160 − 200)" not in request.failure_reply and "-20%" not in request.failure_reply


@pytest.mark.parametrize("omitted", [False, True])
def test_empty_calculation_list_cannot_discard_verified_work_from_an_earlier_repair_attempt(ctx, omitted):
    fail_after_valid_calculation(ctx)
    payload = params(calculations=[]).model_dump()
    if omitted:
        del payload["calculations"]
    result = plugin.conclude(ctx, plugin.ConcludeParams.model_validate(payload))
    assert result.final and "-16.6667%" in result.reply
    evidence = saved(ctx, result)["object"]["answer"]["delivery_evidence"]
    assert evidence["calculations"][0]["before"] == "120" and evidence["calculations"][0]["after"] == "100"


def test_a_bad_replacement_calculation_does_not_overwrite_earlier_valid_pending_work(ctx):
    fail_after_valid_calculation(ctx)
    bad = params()
    bad.calculations[0].after = Decimal(99)
    with pytest.raises(ValueError):
        plugin.conclude(ctx, bad)
    request, payload = completion_after_failed_claim(ctx)
    assert payload["validated_calculations"][0]["after"] == "100"
    assert "-16.6667%" in request.failure_reply


def test_review_context_names_actual_source_url_without_treating_requested_list_as_exhaustive(ctx):
    url = "https://example.org/extra-original"
    ref = read_source(ctx, "A separately retrieved rule has its own URL.", url)
    contexts = plugin._review_contexts(ctx, [{"ref": ref, "field": "text", "offset": 0,
        "text": "A separately retrieved rule has its own URL."}])
    assert contexts and all(row["source_url"] == url for row in contexts if row["ref"] == ref)


def test_omitted_objection_response_is_saved_as_unanswered_not_supported(ctx):
    check = params().model_dump()["checks"][0]
    del check["response"]
    result = plugin.conclude(ctx, params(checks=[check]))
    report = saved(ctx, result)
    answer = report["object"]["answer"]
    assert result.final and any(gap.startswith("Objection not yet answered: Other causes") for gap in answer["gaps"])
    claim = next(iter(report["object"]["assessments"].values())) if isinstance(report["object"]["assessments"], dict) \
        else report["object"]["assessments"][0]
    assert claim["response"] == ""
    assert any(issue["code"] in {"countercheck_not_recorded", "unhandled_counterevidence"} and issue["blocking"]
               for issue in report["issues"])
    assert report["readiness"] == "open"


def test_calculation_error_lists_quote_numbers_and_rejects_derived_inputs(ctx):
    with pytest.raises(ValueError) as exc:
        plugin.conclude(ctx, params(calculations=[{"operation": "difference", "before": 20, "after": 15, "quote": QUOTE}]))
    assert "Numbers in this quote: ['120', '100']" in str(exc.value) and "not derived" in str(exc.value)
