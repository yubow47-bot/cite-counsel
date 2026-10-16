"""Offline checks for refreshed judgments and mixed text/raster PDFs."""

import io

import pytest

from core import quote_check, research as research_core
from core.tool_contracts import Field, Record
from harness.core import Harness
from local_tools import file_extractor
from local_tools.source_status import SourceRequestError
from plugins import a2aj, research


def test_fetching_judgment_supersedes_source_and_invalidates_excerpt(tmp_path, monkeypatch):
    harness = Harness({p.name: p for p in (a2aj.PLUGIN, research.PLUGIN)},
                      model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    initial_text = "[1] Original factual finding."
    original = session.records.put(Record("jurisprudence", {
        "neutral_citation": Field("2024 SCC 1", "database", source_id="a2aj:example"),
        "full_text": Field(initial_text, "database", source_id="a2aj:example"),
    }, "a2aj", "example"))
    excerpt = research.excerpt(harness.context(session, "research"), research.ExcerptParams(
        ref=original, start=0, end=len(initial_text), quote=initial_text)).content["ref"]
    monkeypatch.setattr(quote_check, "judgment", lambda citation, **kwargs: {
        "text": "[1] Corrected factual finding.", "url": "https://example.test/judgment"})
    refreshed = a2aj.full_text(harness.context(session, "a2aj"), a2aj.FullTextParams(ref=original)).content
    assert refreshed["replaces"] == original
    assert session.records.current_ref(original) == refreshed["ref"]
    assert session.records.get_exact(original).fields["full_text"].value == initial_text
    assert research_core.inspect(session.records, excerpt)["stale"] is True
    # Repeating the original handle revises the current source, not a branch.
    again = a2aj.full_text(harness.context(session, "a2aj"), a2aj.FullTextParams(ref=original)).content
    assert again["replaces"] == refreshed["ref"]
    assert session.records.current_ref(original) == again["ref"]


def test_judgment_request_failure_remains_a_structured_source_error(tmp_path, monkeypatch):
    harness = Harness({"a2aj": a2aj.PLUGIN}, model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    original = session.records.put(Record("jurisprudence", {
        "neutral_citation": Field("2024 SCC 2", "database", source_id="a2aj:example")}, "a2aj", "example"))

    def fail(*args, **kwargs):
        raise SourceRequestError("a2aj", "/fetch cases", reason="timeout")

    monkeypatch.setattr("local_tools.a2aj_api._request_results", fail)
    result = a2aj.full_text(harness.context(session, "a2aj"), a2aj.FullTextParams(ref=original))
    assert result.content["kind"] == "source_unavailable"
    assert result.content["reason"] == "timeout"
    assert session.records.current_ref(original) == original


def test_judgment_malformed_envelope_is_not_a_cached_absence(monkeypatch):
    from local_tools import a2aj_api
    from unittest.mock import Mock
    response = Mock()
    response.json.return_value = {}
    monkeypatch.setattr(a2aj_api, "request_with_retry", lambda *a, **kw: response)
    citation = "2024 SCC 98765"
    with pytest.raises(SourceRequestError, match="invalid_response"):
        quote_check.judgment(citation)
    assert citation not in quote_check._cache


def test_explicit_full_text_fetch_bypasses_judgment_cache(tmp_path, monkeypatch):
    from collections import OrderedDict
    from unittest.mock import Mock
    from local_tools.source_status import SourceResults
    citation = "2024 SCC 98766"
    monkeypatch.setattr(quote_check, "_cache", OrderedDict())
    responses = [SourceResults([{"citation_en": citation, "unofficial_text_en": text}])
                 for text in ("[1] Earlier source text.", "[1] Updated source text.")]
    request = Mock(side_effect=responses)
    monkeypatch.setattr("local_tools.a2aj_api._request_results", request)
    assert quote_check.judgment(citation)["text"] == "[1] Earlier source text."
    assert quote_check.judgment(citation)["text"] == "[1] Earlier source text."
    assert request.call_count == 1
    harness = Harness({"a2aj": a2aj.PLUGIN}, model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    original = session.records.put(Record("jurisprudence", {
        "neutral_citation": Field(citation, "database", source_id="a2aj:example")}, "a2aj", "example"))
    refreshed = a2aj.full_text(harness.context(session, "a2aj"), a2aj.FullTextParams(ref=original)).content
    assert session.records.get_exact(refreshed["ref"]).fields["full_text"].value == "[1] Updated source text."
    assert request.call_count == 2
    assert quote_check.judgment(citation)["text"] == "[1] Updated source text."


def _mixed_pdf(path, *, scans=1):
    import fitz
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (600, 700), "white").save(stream, format="PNG")
    with fitz.open() as document:
        ordinary = document.new_page()
        ordinary.insert_textbox(fitz.Rect(72, 72, 520, 200), "Ordinary text without a raster body. " * 8)
        for _ in range(scans):
            page = document.new_page()
            page.insert_textbox(fitz.Rect(50, 30, 540, 100), "Readable repeated court heading and case identifiers. " * 3)
            page.insert_image(fitz.Rect(45, 110, 550, 800), stream=stream.getvalue())
        document.save(path)


def test_readable_header_does_not_hide_scanned_body(tmp_path, monkeypatch):
    path = tmp_path / "mixed.pdf"
    _mixed_pdf(path)
    calls = []

    def ocr(file_path, pages):
        calls.append(pages)
        return {"parts": [{"page": 2, "unit": 2, "kind": "page", "method": "vision",
                           "complete": True, "text": "Scanned body contains the controlling exception."}],
                "errors": [], "titles": []}

    monkeypatch.setattr(file_extractor, "_ocr_pages", ocr)
    result = file_extractor.extract_from_file(str(path))
    assert calls == [[2]]
    assert "controlling exception" in result["raw_text"]
    assert result["coverage"]["status"] == "complete"
    assert result["coverage"]["processed_units"] == [1, 2]


def test_failed_mixed_page_ocr_retains_header_and_reports_missing_body(tmp_path, monkeypatch):
    path = tmp_path / "mixed.pdf"
    _mixed_pdf(path)
    monkeypatch.setattr(file_extractor, "_ocr_pages", lambda *args: {
        "parts": [], "errors": [{"page": 2, "reason": "vision_extraction_failed"}], "titles": []})
    result = file_extractor.extract_from_file(str(path))
    assert "Readable repeated court heading" in result["raw_text"]
    assert result["coverage"]["status"] == "partial"
    assert result["coverage"]["missing_units"] == [2]
    assert result["coverage"]["processed_units"] == [1]


def test_pure_text_pdf_does_not_trigger_ocr(tmp_path, monkeypatch):
    import fitz
    path = tmp_path / "text.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 520, 600), "Native PDF text remains directly readable. " * 20)
        document.save(path)
    monkeypatch.setattr(file_extractor, "_ocr_pages", lambda *a: pytest.fail("Pure text page should not use OCR"))
    result = file_extractor.extract_from_file(str(path))
    assert result["coverage"]["status"] == "complete"
    assert "directly readable" in result["raw_text"]


def test_scanned_body_limit_keeps_unprocessed_pages_missing(tmp_path, monkeypatch):
    path = tmp_path / "many-mixed.pdf"
    _mixed_pdf(path, scans=4)
    calls = []

    def ocr(file_path, pages):
        calls.append(pages)
        return {"parts": [{"page": number, "unit": number, "kind": "page", "method": "vision",
                           "complete": True, "text": f"Scanned evidence page {number}."} for number in pages],
                "errors": [], "titles": []}

    monkeypatch.setattr(file_extractor, "_ocr_pages", ocr)
    result = file_extractor.extract_from_file(str(path))
    assert calls == [[2, 3, 4]]
    assert result["coverage"]["missing_units"] == [5]
    assert result["coverage"]["status"] == "partial"


def test_large_image_with_existing_body_text_layer_does_not_repeat_ocr(tmp_path, monkeypatch):
    import fitz
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (600, 700), "white").save(stream, format="PNG")
    path = tmp_path / "existing-ocr.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_image(fitz.Rect(45, 50, 550, 800), stream=stream.getvalue())
        page.insert_textbox(fitz.Rect(72, 72, 520, 740), "\n".join(
            f"Text layer line {number} contains already extracted evidence." for number in range(40)), fontsize=10)
        document.save(path)
    monkeypatch.setattr(file_extractor, "_ocr_pages", lambda *a: pytest.fail("Existing body text layer should not repeat OCR"))
    result = file_extractor.extract_from_file(str(path))
    assert result["coverage"]["status"] == "complete"
    assert "already extracted evidence" in result["raw_text"]
