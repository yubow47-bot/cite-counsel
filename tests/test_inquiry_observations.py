"""Safe fallback content is attributed source wording, not model-written facts."""
import json

import pytest
from pydantic import ValidationError

from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from harness.receipts import issue
from plugins.inquiry.observations import Observation, evaluate_observations, render_observations


QUOTE = "A payment may be due if the stated conditions are met."
TEXT = "Background. " + QUOTE + " Exceptions remain applicable."


@pytest.fixture
def ctx(tmp_path):
    harness = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    user = "This is a fictional exercise: the count changed from 20 to 10."
    session.messages.append({"role": "user", "content": user})
    session.timeline.append({"role": "user", "text": user})
    session.records.put(Record("document", {
        "text": Field(TEXT, "extracted"),
        "summary": Field("Payment is definitely due.", "model"),
    }, "fixture", "rule"))
    return harness.context(session, "inquiry")


def read(ctx, ref="rec_1", field="text", offset=0, limit=4000):
    ctx._harness._execute_once(ctx.session, {"function": {
        "name": "record__read", "arguments": json.dumps({
            "ref": ref, "field": field, "offset": offset, "limit": limit})}}, {})
    return ctx.session.tool_receipts[-1]


def test_source_observation_binds_actual_wording_without_changing_store(ctx):
    receipt = read(ctx)
    before = list(ctx.records.all())
    results = evaluate_observations(ctx, [Observation(source_ref="rec_1", quote=QUOTE)])
    assert list(ctx.records.all()) == before
    bound = results[0]["provenance"]
    assert bound["source_ref"] == "rec_1" and bound["field"] == "text"
    assert TEXT[bound["start"]:bound["end"]] == QUOTE
    assert bound["fingerprint"] == ctx.records.fingerprint("rec_1")
    assert bound["receipt_ids"] == [receipt["id"]]
    assert not results[0]["semantic_verification"]
    rendered = render_observations(results)
    assert "来源记载 (rec_1)" in rendered and "> " + QUOTE in rendered
    assert "definitely" not in rendered


def test_source_acquisition_is_sufficient_but_never_asserts_inspection(ctx):
    receipt = issue(ctx.session, ctx._harness.plugins, "web__fetch", {"record": {"ref": "rec_1"}})
    results = evaluate_observations(ctx, [{"source_ref": "rec_1", "quote": QUOTE}])
    assert results[0]["receipt_ids"] == [receipt["id"]]
    assert "inspected" not in results[0]
    assert render_observations(results, zh=False).startswith("The source states")


def test_user_report_keeps_full_context_and_is_not_promoted_to_source_fact(ctx):
    quote = "fictional exercise: the count changed from 20 to 10"
    results = evaluate_observations(ctx, [{"quote": quote}])
    bound = results[0]["provenance"]
    assert bound["kind"] == "user_reported" and bound["message_index"] == 0
    assert not bound["receipt_ids"] and "source_ref" not in bound
    assert ctx.user_texts()[0][bound["start"]:bound["end"]] == quote
    assert render_observations(results).startswith("用户提供")
    assert render_observations(results, zh=False).startswith("You provided")


@pytest.mark.parametrize("failure", ["paraphrase", "inference", "assistant", "model_field", "missing_receipt", "partial_read"])
def test_model_fact_or_unseen_text_cannot_survive_as_a_grounded_observation(ctx, failure):
    spec = {"source_ref": "rec_1", "quote": QUOTE}
    if failure == "paraphrase":
        spec = {"quote": "The user says there were 10 fewer events."}
    elif failure == "inference":
        spec = {"quote": "A common cause produced the decline."}
    elif failure == "assistant":
        ctx.session.messages.append({"role": "assistant", "content": "Payment is definitely due."})
        ctx.session.timeline.append({"role": "assistant", "text": "Payment is definitely due."})
        spec = {"quote": "Payment is definitely due."}
    elif failure == "model_field":
        read(ctx, field="summary")
        spec = {"source_ref": "rec_1", "field": "summary", "quote": "Payment is definitely due."}
    elif failure == "partial_read":
        read(ctx, offset=TEXT.index(QUOTE), limit=len(QUOTE) - 1)
    before = list(ctx.records.all())
    with pytest.raises(ValueError):
        evaluate_observations(ctx, [spec])
    assert list(ctx.records.all()) == before


@pytest.mark.parametrize("text, quote", [(QUOTE + " Other section. " + QUOTE, QUOTE), ("aaaa", "aaa")])
def test_ambiguous_source_quote_requires_more_context(ctx, text, quote):
    ref = ctx.records.put(Record("document", {"text": Field(text, "extracted")}, "fixture", "repeated"))
    read(ctx, ref=ref)
    with pytest.raises(ValueError, match="uniqu"):
        evaluate_observations(ctx, [{"source_ref": ref, "quote": quote}])


def test_observation_schema_does_not_accept_free_form_fact_or_binding_fields(ctx):
    with pytest.raises(ValidationError):
        Observation(quote="20 to 10", text="The policy caused the decline.")
    with pytest.raises(ValidationError):
        Observation(quote="20 to 10", receipt_ids=["invented"])
    with pytest.raises(ValueError):
        evaluate_observations(ctx, [{"quote": "20 to 10", "field": "text"}])
    with pytest.raises(ValueError):
        evaluate_observations(ctx, [{"quote": "  "}])
    with pytest.raises(ValueError):
        evaluate_observations(ctx, [{"quote": "20 to 10"}] * 7)


def test_multiline_quoted_assertion_cannot_escape_attribution(ctx):
    quote = "First source line.\n\n# A heading in the document\nAnother source line."
    ref = ctx.records.put(Record("document", {"text": Field(quote, "extracted")}, "fixture", "multiline"))
    read(ctx, ref=ref)
    result = render_observations(evaluate_observations(ctx, [{"source_ref": ref, "quote": quote}]))
    assert "\n> \n> # A heading in the document\n> Another source line." in result
    assert "\n# A heading" not in result


def test_failed_later_observation_leaves_no_partial_saved_objects(ctx):
    read(ctx)
    before = list(ctx.records.all())
    with pytest.raises(ValueError):
        evaluate_observations(ctx, [{"source_ref": "rec_1", "quote": QUOTE}, {"quote": "An invented fact."}])
    assert list(ctx.records.all()) == before
    assert evaluate_observations(ctx, []) == [] and render_observations([]) == ""
