"""Quoted inputs, real acquisition/read bindings, and reproducible final math."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.records import Store
from plugins.inquiry.calculations import Calculation, bind_quote, evaluate_calculations, render_calculations


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def ctx():
    texts = ["Before policy: 120 cases; after policy: 100 cases. Other region: 100 to 90."]
    return SimpleNamespace(records=Store(), session=SimpleNamespace(tool_receipts=[]), user_texts=lambda: texts)


def calculation(**changes):
    values = {"operation": "percent_change", "before": 120, "after": 100,
              "quote": "Before policy: 120 cases; after policy: 100 cases."}
    values.update(changes)
    return Calculation.model_validate(values)


def source(ctx, text, origin="database"):
    return ctx.records.put(Record("web", {"text": Field(text, origin)}, "fixture", "counts"))


def receipt(ctx, ref, *, category="source", start=0, end=None):
    text = ctx.records.get_exact(ref).fields["text"].value
    end = len(text) if end is None else end
    row = {"id": f"run_{len(ctx.session.tool_receipts) + 1}", "status": "ok", "executed": True,
           "category": category, "refs": [{"ref": ref, "fingerprint": ctx.records.fingerprint(ref)}],
           "observations": [{"ref": ref, "field": "text", "offset": start, "end": end,
                             "origin": ctx.records.get_exact(ref).fields["text"].origin,
                             "text_sha256": digest(text[start:end])}]}
    ctx.session.tool_receipts.append(row)
    return row


def test_user_counts_are_calculated_and_rendered_as_descriptive_only(ctx):
    before = ctx.records.entries()
    results = evaluate_calculations(ctx, [calculation(label="The policy definitely caused this decline")])
    row = results[0]
    assert row["before"] == "120" and row["after"] == "100" and row["delta"] == "-20"
    assert row["percent_change"].startswith("-16.66666666")
    assert row["descriptive_only"] and not row["semantic_verification"]
    assert row["provenance"]["kind"] == "user_reported" and row["receipt_ids"] == []
    assert row["provenance"]["text_sha256"] == digest(ctx.user_texts()[0])
    assert json.loads(json.dumps(results)) == results
    output = render_calculations(results, zh=False)
    assert "(100 − 120) / 120 × 100% = ≈-16.6667%" in output
    assert "user-provided inputs" in output and "descriptive only" in output
    assert "definitely caused" not in output
    assert ctx.records.entries() == before


def test_multiple_operations_keep_each_input_and_result_in_rendered_answer(ctx):
    results = evaluate_calculations(ctx, [calculation(operation="difference"), calculation(operation="ratio"),
                                         calculation(before=100, after=90, quote="Other region: 100 to 90.")])
    output = render_calculations(results)
    assert "100 − 120 = -20" in output and "100 ÷ 120 = ≈0.8333" in output
    assert "(90 − 100) ÷ 100 × 100% = -10%" in output
    assert results[1]["ratio"].startswith("0.833333")
    assert "不证明因果" in output


def test_decimal_arithmetic_does_not_introduce_binary_float_errors(ctx):
    ctx.user_texts = lambda: ["Before 0.1, after 0.3."]
    row = evaluate_calculations(ctx, [calculation(before=0.1, after=0.3, quote=ctx.user_texts()[0])])[0]
    assert row["delta"] == "0.2" and row["percent_change"] == "200"


@pytest.mark.parametrize("value", [True, False, "NaN", "Infinity", "-Infinity", "1e99999", "1e-99999", "1234567890123456789012345678901"])
def test_invalid_or_unbounded_numbers_are_rejected(value):
    with pytest.raises(ValidationError):
        calculation(before=value)


@pytest.mark.parametrize("operation", ["ratio", "percent_change"])
def test_zero_baselines_fail_instead_of_inventing_a_percentage(ctx, operation):
    ctx.user_texts = lambda: ["Before 0; after 100."]
    with pytest.raises(ValueError, match="nonzero baseline"):
        evaluate_calculations(ctx, [calculation(operation=operation, before=0, quote=ctx.user_texts()[0])])
    row = evaluate_calculations(ctx, [calculation(operation="difference", before=0, quote=ctx.user_texts()[0])])[0]
    assert row["delta"] == "100"


@pytest.mark.parametrize("before,after,quote", [(12, 100, "Before policy: 120 cases; after policy: 100 cases."),
                                               (120, 10, "Before policy: 120 cases; after policy: 100 cases."),
                                               (120, 99, "Before policy: 120 cases; after policy: 100 cases."),
                                               (120, 100, "Before policy: 120 cases; after policy: 100.")])
def test_invented_inputs_subtokens_and_paraphrases_are_not_calculable(ctx, before, after, quote):
    with pytest.raises(ValueError):
        evaluate_calculations(ctx, [calculation(before=before, after=after, quote=quote)])


def test_quote_cannot_crop_a_longer_original_number_into_an_input(ctx):
    ctx.user_texts = lambda: ["1200"]
    with pytest.raises(ValueError, match="complete numeric tokens"):
        evaluate_calculations(ctx, [calculation(operation="difference", before=120, after=120, quote="120")])


def test_signed_decimal_and_thousands_tokens_are_preserved(ctx):
    ctx.user_texts = lambda: ["最初−1,200.50，然后1,000.25。"]
    row = evaluate_calculations(ctx, [calculation(operation="difference", before="-1200.5", after="1000.25", quote=ctx.user_texts()[0])])[0]
    assert row["delta"] == "2200.75"
    assert row["numeric_bindings"]["before"]["token"] == "−1,200.50"


def test_source_acquisition_binds_exact_record_fingerprint_and_passage(ctx):
    quote = "Counts fell from 120 to 100."
    ref = source(ctx, "Introduction. " + quote)
    row = receipt(ctx, ref)
    result = evaluate_calculations(ctx, [calculation(quote=quote, source_ref=ref)])[0]
    provenance = result["provenance"]
    assert result["receipt_ids"] == [row["id"]]
    assert provenance["fingerprint"] == ctx.records.fingerprint(ref)
    assert provenance["start"] == len("Introduction. ") and provenance["quote_sha256"] == digest(quote)
    assert provenance["kind"] == "source_bound"
    assert ref in render_calculations([result])


def test_contiguous_real_reads_can_jointly_cover_a_calculation_quote(ctx):
    quote = "Counts fell from 120 to 100."
    ref = source(ctx, quote, "extracted")
    first = receipt(ctx, ref, category="read", end=12)
    second = receipt(ctx, ref, category="read", start=12)
    result = evaluate_calculations(ctx, [calculation(quote=quote, source_ref=ref)])[0]
    assert result["receipt_ids"] == [first["id"], second["id"]]


@pytest.mark.parametrize("failure", ["absent", "failed", "unexecuted", "fingerprint", "unrelated", "gap", "read_hash", "read_origin", "model_field"])
def test_source_numbers_need_actual_acquisition_or_full_valid_read(ctx, failure):
    quote = "Counts fell from 120 to 100."
    ref = source(ctx, quote, "model" if failure == "model_field" else "database")
    row = receipt(ctx, ref, category="read" if failure in {"gap", "read_hash", "read_origin"} else "source")
    if failure == "absent":
        ctx.session.tool_receipts.clear()
    elif failure == "failed":
        row["status"] = "error"
    elif failure == "unexecuted":
        row["executed"] = False
    elif failure == "fingerprint":
        row["refs"][0]["fingerprint"] = "invented"
    elif failure == "unrelated":
        row["refs"][0]["ref"] = source(ctx, quote)
    elif failure == "gap":
        row["observations"][0].update(end=10, text_sha256=digest(quote[:10]))
        receipt(ctx, ref, category="read", start=11)
    elif failure == "read_hash":
        row["observations"][0]["text_sha256"] = "invented"
    elif failure == "read_origin":
        row["observations"][0]["origin"] = "model"
    with pytest.raises(ValueError):
        evaluate_calculations(ctx, [calculation(quote=quote, source_ref=ref)])


def test_a_model_artifact_cannot_be_relabelled_as_numeric_source(ctx):
    ref = ctx.records.put(Artifact("answer", "120 to 100", Derivation((), "fixture")))
    with pytest.raises(ValueError):
        evaluate_calculations(ctx, [calculation(quote="120 to 100", source_ref=ref)])


def test_collection_limit_and_atomic_validation_do_not_change_store(ctx):
    with pytest.raises(ValueError, match="four"):
        evaluate_calculations(ctx, [calculation()] * 5)
    before = ctx.records.entries()
    with pytest.raises(ValueError):
        evaluate_calculations(ctx, [calculation(), calculation(after=99)])
    assert ctx.records.entries() == before
    assert evaluate_calculations(ctx, []) == [] and render_calculations([]) == ""


def test_shared_quote_binding_rejects_ambiguous_fields_and_wrong_field(ctx):
    quote = "120 to 100"
    ref = ctx.records.put(Record("web", {"text": Field(quote, "database"), "summary": Field(quote, "database")}, "fixture", "two"))
    receipt(ctx, ref)
    with pytest.raises(ValueError, match="uniquely"):
        bind_quote(ctx, quote, ref)
    assert bind_quote(ctx, quote, ref, "text")["field"] == "text"
    with pytest.raises(ValueError):
        bind_quote(ctx, quote, ref, "wrong")
    with pytest.raises(ValueError, match="source_ref"):
        bind_quote(ctx, ctx.user_texts()[0], field="text")
