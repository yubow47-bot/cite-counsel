"""Offline tests: whatever the Chatbox fast path cannot finish goes to the original pipeline."""

import json

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import api.main as legacy
from api.chatbox_app import create_app
from core import chatbox_service as service
from tests.test_chatbox_http import settings


def _done(citation, **extra):
    return {"ok": True, "route": "legislation", "status": "done",
            "data": {"citations": [{"citation": citation, "source_type": "legislation", **extra}]}, "error": None}


def test_legacy_envelopes_become_blocks():
    block = service.legacy_blocks(_done("*Criminal Code*, RSC 1985, c C-46.", pinpoint="s 718"))[0]
    assert block["type"] == "citation_result" and block["pinpoint"] == "s 718"
    assert block["verified"] is False and block["warnings"] == [service.LEGACY_NOTE]
    notice = service.legacy_blocks({"status": "unsupported", "data": {}, "error": {"reason": "Not verifiable."}})[0]
    assert (notice["type"], notice["message"], notice["level"]) == ("notice", "Not verifiable.", "warning")


def test_legacy_candidates_keep_their_signature_and_select_through_legacy(monkeypatch):
    store = service.CandidateStore()
    monkeypatch.setattr(service, "candidate_store", store)
    signed = [legacy._selection_candidate({"style_of_cause": f"A v B {i}", "neutral_citation": f"202{i} SCC {i}",
                                           "verified": True}) for i in (1, 2)]
    block = service.legacy_blocks({"status": "needs_selection", "data": {"candidates": signed}})[0]
    assert block["type"] == "candidate_list" and len(block["items"]) == 2
    item = store.get(block["candidate_set_id"], block["access_token"], block["items"][0]["id"])
    assert item == signed[0]


def test_spend_cap_response_is_reported(monkeypatch):
    async def capped(*args):
        return JSONResponse(status_code=503, content={"status": "error", "error": {"reason": "Daily cap reached."}})
    assert service._run_legacy(capped)["error"]["reason"] == "Daily cap reached."


def test_no_verified_fast_result_runs_original_query_pipeline(monkeypatch):
    monkeypatch.setattr(service, "classify_query", lambda text: {"type": "legislation", "normalized": text, "original": text})
    monkeypatch.setattr("local_tools.citation_search.search_citation", lambda *a, **k: [])
    seen = []

    async def original(body, request):
        seen.append(body.input)
        return _done("*Criminal Code*, RSC 1985, c C-46, s 718.2(e).")
    monkeypatch.setattr(legacy, "citation_query", original)
    blocks = service.query_blocks("CCC s 718.2(e)")
    assert seen == ["CCC s 718.2(e)"]
    assert blocks[0]["citation"] == "*Criminal Code*, RSC 1985, c C-46, s 718.2(e)."


def test_fast_path_error_falls_back_to_original(monkeypatch):
    def boom(text):
        raise RuntimeError("offline")
    monkeypatch.setattr(service, "classify_query", boom)
    monkeypatch.setattr(service, "legacy_query_blocks", lambda text: [service.notice("legacy ran")])
    assert service.query_blocks("anything")[0]["message"] == "legacy ran"


def test_record_the_template_cannot_render_goes_to_original_formatter(monkeypatch):
    seen = []

    async def original_select(body):
        seen.append(body.candidates[0])
        return _done("*Canadian Charter of Rights and Freedoms*, s 7, Part I of the *Constitution Act, 1982*.")
    monkeypatch.setattr(legacy, "citation_select", original_select)
    charter = {"statute_title": "Canadian Charter of Rights and Freedoms", "pinpoint": "s 7", "verified": True}
    block = service.record_blocks(charter)[0]
    assert "Constitution Act, 1982" in block["citation"]
    assert legacy._candidate_signature_valid(seen[0])


def test_case_record_renders_pinpoint_as_a_user_field():
    """A pinpoint comes from the request, never the record, so it keeps the result unverified."""
    block = service.record_blocks({"style_of_cause": "R v Gladue", "reporter": "[1999] 1 SCR 688",
                                   "pinpoint": "at para 93", "verified": True})[0]
    assert block["citation"] == "*R v Gladue*, [1999] 1 SCR 688 at para 93."
    assert block["verified"] is False
    origins = {field["name"]: field["origin"] for field in block["fields"] if field["value"]}
    assert origins == {"style_of_cause": "database", "reporter": "database", "pinpoint": "user"}


def test_case_record_without_a_pinpoint_is_verified_field_by_field():
    block = service.record_blocks({"style_of_cause": "R v Gladue", "reporter": "[1999] 1 SCR 688",
                                   "verified": True})[0]
    assert block["citation"] == "*R v Gladue*, [1999] 1 SCR 688." and block["verified"] is True
    assert all(field["origin"] == "database" for field in block["fields"] if field["value"])


def test_unsupported_document_type_uses_original_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr("local_tools.file_extractor.classify_document_type", lambda _: "government_document")
    monkeypatch.setattr("core.mcgill_engine.format_citation",
                        lambda fields, doc_type=None: f"Gov doc via {doc_type}.")
    blocks = service.extracted_blocks({"raw_text": "Report of the Standing Committee on Justice " * 5}, False)
    assert blocks[0]["citation"] == "Gov doc via government_document."


def test_identifier_miss_uses_original_url_route(monkeypatch):
    monkeypatch.setattr("local_tools.crossref_api.fetch_crossref", lambda doi: None)
    seen = []

    async def original(body):
        seen.append(body.doi)
        return {"status": "unsupported", "data": {}, "error": {"reason": "We couldn't process this DOI."}}
    monkeypatch.setattr(legacy, "extract_url", original)
    assert service.identifier_blocks("doi", "10.1/x")[0]["message"] == "We couldn't process this DOI."
    assert seen == ["10.1/x"]


def test_short_webpage_body_is_refused_like_original():
    blocks = service.extracted_blocks({"url": "https://example.gc.ca", "raw_text": "Loading..."}, False, webpage=True)
    assert len(blocks) == 1 and "截图" in blocks[0]["message"]


def test_feedback_endpoint_is_removed():
    with TestClient(create_app(settings())) as client:
        assert client.post("/api/chatbox/feedback", json={"kind": "message", "note": "x"}).status_code in (404, 405)


def test_warmup_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "warm_up", lambda: calls.append(1) or {"warmed": ["a2aj"], "failed": []})
    with TestClient(create_app(settings())) as client:
        assert client.get("/api/chatbox/warmup").json()["warmed"] == ["a2aj"]
        assert client.get("/api/chatbox/warmup").json()["cached"] is True
    assert calls == [1]
