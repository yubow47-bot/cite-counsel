"""Locations and verification must survive composition without changing work."""

import json

import pytest

from core import bibliography, quote_check
from core.tool_contracts import Artifact, Derivation, Field, Record, is_grounded
from harness.core import Harness
from plugins import bibliography as bibliography_plugin, mcgill, quote

QUOTE = "Only Alpha contains these exact words."


@pytest.fixture
def workspace(tmp_path):
    plugins = {p.name: p for p in (mcgill.PLUGIN, quote.PLUGIN, bibliography_plugin.PLUGIN)}
    h = Harness(plugins, model="test", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    session.loaded = list(plugins)
    session.messages.append({"role": "user", "content": QUOTE})
    return h, session


def call(workspace, name, **params):
    h, session = workspace
    return h._execute(session, {"id": "test", "type": "function", "function": {
        "name": name, "arguments": json.dumps(params)}})[1]


def case(name="Alpha", text=None):
    fields = {"style_of_cause": Field(f"R v {name}", "database", source_id=f"a2aj:{name}"),
              "neutral_citation": Field("2020 SCC 1" if name == "Alpha" else "2021 SCC 2",
                                        "database", source_id=f"a2aj:{name}")}
    if text is not None:
        fields["full_text"] = Field(text, "database", source_id=f"a2aj:{name}")
    return Record("jurisprudence", fields, "a2aj", name)


def test_replacing_work_detaches_previous_text_and_pinpoint(workspace):
    _, session = workspace
    alpha = session.records.put(case(text=f"[1] Introduction.\n[2] {QUOTE}"))
    beta = session.records.put(case("Beta", "[1] A different judgment."))
    checked = call(workspace, "quote__check", ref=alpha, quote=QUOTE).content
    composed = call(workspace, "record__compose", base_ref=alpha, fields=[
        {"name": "style_of_cause", "value": "R v Beta", "source": beta},
        {"name": "neutral_citation", "value": "2021 SCC 2", "source": beta},
    ]).content
    changed = session.records.get(composed["ref"], Record)
    assert composed["identity_changed"] and "full_text" not in changed.fields
    assert changed.record_id != "Alpha"
    assert "error" in call(workspace, "quote__check", ref=composed["ref"], quote=QUOTE).content
    rejected = call(workspace, "mcgill__cite", ref=composed["ref"], pinpoint_from=checked["pinpoint_ref"])
    assert "not located in this cited work" in rejected.content["error"]


def test_adding_identity_from_another_work_also_detaches_body(workspace):
    _, session = workspace
    alpha_record = case(text=f"[1] {QUOTE}")
    alpha = session.records.put(Record("jurisprudence", {
        name: field for name, field in alpha_record.fields.items() if name != "neutral_citation"
    }, "a2aj", "Alpha"))
    beta = session.records.put(case("Beta"))
    result = call(workspace, "record__compose", base_ref=alpha, fields=[
        {"name": "neutral_citation", "value": "2021 SCC 2", "source": beta}]).content
    assert result["identity_changed"]
    assert "full_text" not in session.records.get(result["ref"]).fields


def test_fresh_compositions_have_separate_work_identity(workspace):
    refs = [call(workspace, "record__compose", record_type="jurisprudence", fields=[
        {"name": "style_of_cause", "value": "R v Alpha"},
        {"name": "neutral_citation", "value": "2020 SCC 1"}]).content["ref"] for _ in range(2)]
    assert workspace[1].records.get(refs[0]).record_id != workspace[1].records.get(refs[1]).record_id


def test_same_work_different_text_cannot_reuse_location(workspace):
    _, session = workspace
    first = session.records.put(case(text=f"[1] Introduction.\n[2] {QUOTE}"))
    checked = call(workspace, "quote__check", ref=first, quote=QUOTE).content
    second = session.records.put(case(text=f"[1] {QUOTE}\n[2] Revised."))
    result = call(workspace, "mcgill__cite", ref=second, pinpoint_from=checked["pinpoint_ref"])
    assert "text version has changed" in result.content["error"]


def test_old_location_is_rejected_after_text_version_superseded(workspace):
    _, session = workspace
    first = session.records.put(case(text=f"[1] {QUOTE}"))
    checked = call(workspace, "quote__check", ref=first, quote=QUOTE).content
    second = session.records.put(case(text="[1] Revised judgment text."), supersedes=first)
    result = call(workspace, "mcgill__cite", ref=second, pinpoint_from=checked["pinpoint_ref"])
    assert "text version has changed" in result.content["error"]


def test_extracted_quote_keeps_its_original_text_and_origin(workspace):
    _, session = workspace
    raw = "[1] Introduction.\n[2] The court’s   words are preserved exactly."
    ref = session.records.put(Record("website", {
        "title": Field("Judgment page", "extracted", source_id="page:1"),
        "url": Field("https://example.test/judgment", "extracted", source_id="page:1"),
        "text": Field(raw, "extracted", source_id="page:1"),
    }, "web", "page:1"))
    result = call(workspace, "quote__check", ref=ref, quote="The court's words are preserved exactly.").content
    assert result["verdict"] == "exact" and result["pinpoint"] == "at para 2"
    source = session.records.get(result["finding"]).derivation.inputs[1]
    assert source.value == raw and source.origin == "extracted"
    pinpoint = session.records.get(result["pinpoint_ref"], Artifact)
    assert not is_grounded(pinpoint.derivation)
    cited = call(workspace, "mcgill__cite", ref=ref).content
    assert not cited["verified"]


def test_partial_extraction_absence_is_inconclusive(workspace):
    _, session = workspace
    ref = session.records.put(Record("document", {
        "text": Field("The beginning of a partial document.", "extracted"),
        "extraction_coverage": Field(json.dumps({"status": "partial", "missing_units": [4]}), "extracted")
    }, "file", "attachment-1"))
    result = call(workspace, "quote__check", ref=ref, quote=QUOTE).content
    finding = session.records.get(result["finding"])
    assert result["verdict"] == "not_found" and result["coverage"] == "partial"
    assert finding.verdict == "inconclusive" and finding.coverage == "partial"


def test_ellipsis_retries_later_start_before_reporting_not_found():
    text = "[1] Repeated opening " + "x" * 6100 + "\n[2] Repeated opening and the ending."
    located = quote_check.locate(text, "Repeated opening ... the ending.")
    assert located.verdict == "exact" and located.paragraphs == (2,)


def test_casefold_expansion_preserves_source_offsets():
    text = "[1] Stra\u00dfe appears earlier.\n[2] The Exact quotation is here."
    located = quote_check.locate(text, "the exact quotation is here.")
    assert located.verdict == "case_differs" and located.paragraphs == (2,)
    assert text[located.start:located.end] == "The Exact quotation is here."


def test_cleaned_citation_values_do_not_break_bibliography_audit(workspace):
    _, session = workspace
    values = {"author": "Hart, H. L. A.", "title": "The Concept of Law.", "place": "Oxford.",
              "publisher": "Oxford University Press.", "year": "1994"}
    ref = session.records.put(Record("book", {name: Field(value, "database", source_id="ol:1")
                                             for name, value in values.items()}, "openlibrary", "OL1"))
    citation = call(workspace, "mcgill__cite", ref=ref).content
    assert citation["verified"]
    assert citation["source_grounded"] and citation["legal_verification"] is False
    result = call(workspace, "bibliography__build", refs=[citation["ref"]])
    assert result.content["unverified"] == 0 and result.content["grounded"]
    assert result.content["source_grounded"] and result.content["legal_verification"] is False
    assert result.blocks[0]["note"] == ("Every entry's retained fields trace to database records. "
                                       "Legal accuracy, authority and applicability have not been assessed.")


def test_bibliography_uses_audited_citation_fields_not_stale_meta(workspace):
    h, session = workspace
    meta = {"source_type": "jurisprudence", "base": None, "fields": {
        "style_of_cause": {"value": "R v Fabricated", "origin": "database", "source_id": "fake"},
        "neutral_citation": {"value": "2020 SCC 1", "origin": "database", "source_id": "fake"}}}
    artifact = Artifact("citation_text", "fake", Derivation(tuple(
        Field(field["value"], "database", source_id="fake") for field in meta["fields"].values()),
        "cite.render.v1", tuple(meta["fields"])))
    ref = h.context(session, "mcgill").save(artifact, meta=meta)
    result = call(workspace, "bibliography__build", refs=[ref])
    assert result.content["unverified"] == 1 and not result.content["grounded"]
    assert "do not trace to database records" in result.blocks[0]["note"]
    assert result.content["source_grounded"] is False and result.content["legal_verification"] is False


def test_bibliography_proof_tracks_only_the_selected_duplicate():
    fields = {"style_of_cause": {"value": "R v Alpha", "origin": "database", "source_id": "a2aj:Alpha"},
              "neutral_citation": {"value": "2020 SCC 1", "origin": "database", "source_id": "a2aj:Alpha"}}
    verified = {"source_type": "jurisprudence", "base": None, "fields": fields}
    unverified = {**verified, "fields": {name: {**field, "origin": "model"} for name, field in fields.items()}}
    result = bibliography.build([unverified, verified])
    assert result["count"] == 1 and result["unverified"] == 0 and result["grounded"]
    assert len(result["artifact"].derivation.inputs) == 1
