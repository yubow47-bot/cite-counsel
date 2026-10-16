"""A unique exact quote can be located without guessed character offsets."""

import pytest

from core import research
from core.tool_contracts import Artifact, Field, Record
from harness.core import Harness
from plugins import research as plugin


@pytest.fixture
def ctx(tmp_path):
    harness = Harness({"research": plugin.PLUGIN}, model="offline", config_path=tmp_path / "config.json")
    session, _ = harness.sessions.start()
    return harness.context(session, "research")


def source(ctx, text, *, field="full_text", origin="database"):
    return ctx.records.put(Record("case", {field: Field(text, origin, source_id="synthetic:quote")},
                                 "synthetic", "quote-selection"))


def test_unique_quote_locates_exact_span_and_preserves_binding_provenance(ctx):
    quote = "The application is dismissed."
    text = "Header\n[12] " + quote + "\nFurther reasons."
    ref = source(ctx, text)
    result = plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote=quote, speaker="court")).content
    binding = result["object"]["source"]
    assert binding["start"] == text.index(quote) and binding["end"] == text.index(quote) + len(quote)
    assert binding["ref"] == ref and binding["fingerprint"] == ctx.records.fingerprint(ref)
    assert binding["work"] == {"provider": "synthetic", "record_id": "quote-selection", "source_type": "case"}
    assert binding["field_sha256"] == research.digest(text) and binding["quote_sha256"] == research.digest(quote)
    assert binding["origin"] == "database" and binding["source_id"] == "synthetic:quote"
    artifact = ctx.records.get_exact(result["ref"], Artifact)
    assert artifact.derivation.inputs[0].span == (text.index(quote), text.index(quote) + len(quote))
    assert artifact.derivation.inputs[0].origin == "extracted"
    assert result["mechanical_binding"] == "intact" and result["legal_verification"] is False


def test_quote_only_uses_selected_exact_field_without_changing_origin(ctx):
    ref = source(ctx, "Before. A unique passage. After.", field="text", origin="extracted")
    result = plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote="A unique passage.")).content
    assert result["object"]["source"]["field"] == "text"
    assert result["object"]["source"]["origin"] == "extracted"


@pytest.mark.parametrize("text,quote", [("Repeated. Repeated.", "Repeated."), ("aaa", "aa")])
def test_repeated_including_overlapping_quote_requires_explicit_offsets(ctx, text, quote):
    ref = source(ctx, text)
    count = len(ctx.records.all())
    with pytest.raises(ValueError, match="more than once.*record__search.*both start and end"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote=quote))
    assert len(ctx.records.all()) == count


def test_absent_quote_is_not_approximately_matched(ctx):
    ref = source(ctx, "The application was allowed.")
    count = len(ctx.records.all())
    with pytest.raises(ValueError, match="does not occur.*record__search"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote="The application is allowed."))
    assert len(ctx.records.all()) == count


@pytest.mark.parametrize("offsets", [{"start": 0}, {"end": 4}])
def test_one_offset_is_rejected_instead_of_relocated(ctx, offsets):
    ref = source(ctx, "Some text.")
    with pytest.raises(ValueError, match="both start and end"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote="Some", **offsets))


def test_wrong_explicit_span_still_fails_even_when_quote_occurs_uniquely_elsewhere(ctx):
    ref = source(ctx, "Before. Unique. After.")
    with pytest.raises(ValueError, match="does not exactly match"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, start=0, end=7, quote="Unique."))


def test_repeated_quote_can_select_intended_occurrence_with_explicit_span(ctx):
    ref = source(ctx, "Repeated. Repeated.")
    result = plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote="Repeated.", start=10, end=19)).content
    assert result["object"]["source"]["start"] == 10


def test_unique_quote_cannot_make_model_text_an_original_source(ctx):
    ref = source(ctx, "A unique model sentence.", origin="model")
    with pytest.raises(ValueError, match="model-origin"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, quote="A unique model sentence."))
