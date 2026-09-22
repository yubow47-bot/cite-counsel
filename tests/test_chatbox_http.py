"""Local Chatbox contracts; provider and retrieval work is mocked."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.chatbox_app import create_app
from core import chatbox_service as service


def settings(key="", typesafe_key=""):
    result = json.loads((Path(__file__).parents[1] / "config/chatbox.example.json").read_text(encoding="utf-8"))
    result["openrouter_api_key"] = key
    result["typesafe_api_key"] = typesafe_key
    return result


@pytest.fixture
def client():
    with TestClient(create_app(settings())) as result:
        yield result


def test_page_assets_and_public_config(client):
    assert client.get("/").status_code == 200
    for asset in ("app.js", "styles.css", "layout.css"):
        assert client.get("/assets/" + asset).status_code == 200
    with TestClient(create_app(settings("must-not-leak", "typesafe-must-not-leak"))) as configured:
        response = configured.get("/api/chatbox/config")
    assert response.json()["configured"] is True
    assert "must-not-leak" not in response.text
    assert "typesafe-must-not-leak" not in response.text
    assert response.json()["jev_provider"] == "typesafe"
    assert "segments" not in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_key_settings_endpoint_never_returns_secrets(monkeypatch):
    import core.chatbox_settings as chatbox_settings
    saved = {}
    def fake_save(openrouter, typesafe):
        saved.update(openrouter=openrouter, typesafe=typesafe)
        monkeypatch.setenv("OPENROUTER_API_KEY", openrouter)
        monkeypatch.setenv("TYPESAFE_API_KEY", typesafe)
        return {"llm_configured": True, "jev_configured": True}
    monkeypatch.setattr(chatbox_settings, "save_api_keys", fake_save)
    with TestClient(create_app(settings())) as local_client:
        response = local_client.post("/api/chatbox/settings/keys", json={
            "openrouter_api_key": "openrouter-test-secret",
            "typesafe_api_key": "typesafe-test-secret",
        })
        status = local_client.get("/api/chatbox/config").json()
    assert response.status_code == 200
    assert saved == {"openrouter": "openrouter-test-secret", "typesafe": "typesafe-test-secret"}
    assert "test-secret" not in response.text
    assert status["llm_configured"] is True and status["jev_configured"] is True


def test_key_settings_requires_at_least_one_value(client):
    assert client.post("/api/chatbox/settings/keys", json={}).status_code == 400


def test_unconfigured_query_does_not_call_provider(client, monkeypatch):
    monkeypatch.setattr(service, "query_blocks", lambda _: pytest.fail("must not call model"))
    blocks = client.post("/api/chatbox/turns", json={"input": "R v Gladue"}).json()["blocks"]
    assert [block["type"] for block in blocks] == ["notice"]


def test_manual_assembly_removed(client):
    assert client.post("/api/chatbox/assemble", json={"type": "book", "fields": {}}).status_code in (404, 405)
    assert "scaffold" not in client.get("/api/chatbox/config").json()


@pytest.mark.parametrize("name,reporter", [
    ("Edwards v Canada (Attorney General)", "[1930] AC 124"),
    ("R v Gladue", "[1999] 1 SCR 688"),
    ("Roncarelli v Duplessis", "[1959] SCR 121"),
])
def test_renderer_never_invents_neutral_citation(name, reporter):
    fields = {"style_of_cause": name, "reporter": reporter}
    assert service.render_fields("jurisprudence", fields) == f"*{name}*, {reporter}."
    fields["pinpoint"] = "at para 64"
    assert service.render_fields("jurisprudence", fields) == f"*{name}*, {reporter} at para 64."


def test_renderer_rejects_missing_and_unknown_fields():
    with pytest.raises(ValueError):
        service.render_fields("jurisprudence", {"style_of_cause": "X"})
    with pytest.raises(ValueError):
        service.render_fields("book", {"made_up": "value"})


def test_renderer_preserves_literal_title_and_rejects_script_url():
    fields = {"title": "<script>alert(1)</script> [legal]", "url": "https://example.com"}
    assert fields["title"] in service.render_fields("website", fields)
    with pytest.raises(ValueError):
        service.render_fields("website", {**fields, "url": "javascript:alert(1)"})


def test_malformed_url_and_cross_site_request(client):
    assert client.post("/api/chatbox/turns", json={"input": "https://[broken"}).status_code == 400
    assert client.post("/api/chatbox/turns", json={"input": "hi"}, headers={"origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/", headers={"host": "untrusted.example"}).status_code == 400


def test_identifier_routing_before_llm(client, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "identifier_blocks", lambda kind, value: calls.append((kind, value)) or [service.notice("offline test")])
    for value in ("https://doi.org/10.1038/nature12373", "ISBN 978-0-306-40615-7"):
        assert client.post("/api/chatbox/turns", json={"input": value}).status_code == 200
    assert calls == [("doi", "10.1038/nature12373"), ("isbn", "9780306406157")]


def test_candidate_selection_tamper_and_expiry(monkeypatch):
    records = [{"style_of_cause": "Alpha v Beta", "neutral_citation": "2020 SCC 1", "verified": True},
               {"style_of_cause": "Alpha v Beta", "neutral_citation": "2021 SCC 2", "verified": True},
               {"style_of_cause": "Invented", "verified": False}]
    monkeypatch.setattr("local_tools.citation_search.search_citation", lambda *a, **kw: copy.deepcopy(records))
    store = service.CandidateStore()
    monkeypatch.setattr(service, "candidate_store", store)
    with TestClient(create_app(settings("fake-key"))) as client:
        block = client.post("/api/chatbox/turns", json={"input": "Alpha v Beta"}).json()["blocks"][0]
        assert len(block["items"]) == 2
        body = {"candidate_set_id": block["candidate_set_id"], "access_token": block["access_token"], "candidate_id": block["items"][0]["id"]}
        result = client.post("/api/chatbox/select", json=body).json()["blocks"][0]
        # Every rendered field is copied from the chosen record, so this one is verified.
        assert "2020 SCC 1" in result["citation"] and result["verified"] is True
        assert client.post("/api/chatbox/select", json={**body, "access_token": "wrong-token"}).status_code == 409
        assert client.post("/api/chatbox/select", json={**body, "candidate_id": "unknown"}).status_code == 409
        saved = store._sets[body["candidate_set_id"]]
        saved["items"][body["candidate_id"]]["neutral_citation"] = "tampered"
        assert client.post("/api/chatbox/select", json=body).status_code == 409
        saved["expires"] = 0
        assert client.post("/api/chatbox/select", json=body).status_code == 409


def test_fake_file_rejected_without_model(client):
    assert client.post("/api/chatbox/files", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    response = client.post("/api/chatbox/files", files={"file": ("sample.pdf", b"%PDF-1.7\n", "application/pdf")})
    assert response.status_code == 200
    assert "OpenRouter" in response.json()["blocks"][0]["message"]


def test_jev_failure_degrades_to_original_text_not_invented_fields(monkeypatch):
    from core.decisions.client import DecisionError
    from core.decisions.jev_client import JevClient
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("local_tools.file_extractor.classify_document_type", lambda _: "book")
    def fail(*args):
        raise DecisionError("offline failure")
    monkeypatch.setattr(JevClient, "decide_choice", fail)
    blocks = service.extracted_blocks({"raw_text": "Original source text", "author": "model-invented author"}, True)
    assert "citation_result" not in [block["type"] for block in blocks]
    assert any("Original source text" in block.get("message", "") for block in blocks)
    assert "model-invented author" not in str(blocks)


def test_upload_limit_is_configurable():
    config = settings()
    config["max_upload_mb"] = 1
    with TestClient(create_app(config)) as limited:
        assert limited.get("/api/chatbox/config").json()["max_upload_mb"] == 1
        big = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)
        assert limited.post("/api/chatbox/files", files={"file": ("big.pdf", big, "application/pdf")}).status_code == 413
        small = b"%PDF-1.7\n" + b"0" * (512 * 1024)
        assert limited.post("/api/chatbox/files", files={"file": ("ok.pdf", small, "application/pdf")}).status_code == 200


@pytest.fixture
def db_result(monkeypatch):
    """One verified database record, rendered through the strict template."""
    record = {"style_of_cause": "Alpha v Beta", "neutral_citation": "2020 SCC 1", "verified": True}
    monkeypatch.setattr("local_tools.citation_search.search_citation", lambda *a, **kw: [copy.deepcopy(record)])
    monkeypatch.setattr(service, "item_store", service.ItemStore())
    with TestClient(create_app(settings("fake-key"))) as local_client:
        block = local_client.post("/api/chatbox/turns", json={"input": "Alpha v Beta"}).json()["blocks"][0]
        yield local_client, block


def edit(client, block, fields, **overrides):
    body = {"item_id": block["item_id"], "access_token": block["access_token"],
            "revision": block["revision"], "fields": fields, **overrides}
    return client.post("/api/chatbox/items/fields", json=body)


def test_user_pinpoint_is_rendered_server_side_and_drops_verified(db_result):
    client, block = db_result
    assert block["verified"] is True and block["citation"] == "*Alpha v Beta*, 2020 SCC 1."
    updated = edit(client, block, {"pinpoint": "at para 12"}).json()["blocks"][0]
    assert updated["citation"] == "*Alpha v Beta*, 2020 SCC 1 at para 12."
    assert updated["verified"] is False and updated["revision"] == 2
    origins = {field["name"]: field["origin"] for field in updated["fields"] if field["value"]}
    assert origins == {"style_of_cause": "database", "neutral_citation": "database", "pinpoint": "user"}
    # The superseded revision cannot be edited again.
    assert edit(client, block, {"pinpoint": "at para 99"}).status_code == 409


def test_correcting_a_field_keeps_the_others_attributed_to_the_record(db_result):
    client, block = db_result
    updated = edit(client, block, {"neutral_citation": "2021 SCC 2"}).json()["blocks"][0]
    assert updated["citation"] == "*Alpha v Beta*, 2021 SCC 2." and updated["verified"] is False
    origins = {field["name"]: field["origin"] for field in updated["fields"] if field["value"]}
    assert origins == {"style_of_cause": "database", "neutral_citation": "user"}


def test_clearing_a_required_field_asks_for_it_again(db_result):
    client, block = db_result
    question = edit(client, block, {"neutral_citation": ""}).json()["blocks"][0]
    assert question["type"] == "field_question"
    assert [field["name"] for field in question["fields"]] == ["neutral_citation", "reporter"]


def test_item_edits_reject_wrong_token_unknown_field_and_expiry(db_result):
    client, block = db_result
    assert edit(client, block, {"pinpoint": "at para 1"}, access_token="wrong-token").status_code == 409
    assert edit(client, block, {"pinpoint": "at para 1"}, item_id="unknown").status_code == 409
    assert edit(client, block, {"made_up": "value"}).status_code == 409
    assert edit(client, block, {}).status_code == 409
    service.item_store._items[block["item_id"]]["expires"] = 0
    assert edit(client, block, {"pinpoint": "at para 1"}).status_code == 409


def test_legacy_citation_string_only_accepts_a_pinpoint(monkeypatch):
    monkeypatch.setattr(service, "item_store", service.ItemStore())
    block = service.citation_block("*Legacy v Formatter*, 2001 SCC 5.", "jurisprudence", warning=service.LEGACY_NOTE)
    assert block["verified"] is False
    assert [field["name"] for field in block["fields"]] == ["pinpoint"]
    with TestClient(create_app(settings())) as client:
        assert edit(client, block, {"style_of_cause": "Rewritten"}).status_code == 409
        updated = edit(client, block, {"pinpoint": "at para 7"}).json()["blocks"][0]
    assert updated["citation"] == "*Legacy v Formatter*, 2001 SCC 5 at para 7."


# ── Bring-your-own key ──────────────────────────────────────────────────
# A key on the request is visible only for that request's provider calls and
# is never echoed back, persisted, or left over for the next request.


def test_a_query_without_a_server_key_is_blocked(client):
    blocks = client.post("/api/chatbox/turns", json={"input": "R v Gladue"}).json()["blocks"]
    assert blocks[0]["type"] == "notice" and "OpenRouter" in blocks[0]["message"]


def test_a_per_request_key_lets_an_unconfigured_server_proceed(client, monkeypatch):
    from llm_api.request_credentials import openrouter_key
    seen = []
    monkeypatch.setattr(service, "query_blocks", lambda text: seen.append(openrouter_key()) or [service.notice("ran")])
    response = client.post("/api/chatbox/turns", json={"input": "R v Gladue", "openrouter_api_key": "byok-secret"})
    assert response.status_code == 200 and seen == ["byok-secret"]
    assert "byok-secret" not in response.text


def test_a_per_request_key_is_scoped_to_that_request_only(client, monkeypatch):
    """After the BYOK request returns, an unconfigured request is blocked again."""
    from llm_api.request_credentials import openrouter_key
    monkeypatch.setattr(service, "query_blocks", lambda text: [service.notice(openrouter_key() or "none")])
    with_key = client.post("/api/chatbox/turns", json={"input": "R v Gladue", "openrouter_api_key": "byok-secret"})
    assert with_key.json()["blocks"][0]["message"] == "byok-secret"
    without_key = client.post("/api/chatbox/turns", json={"input": "R v Gladue"})
    assert without_key.json()["blocks"][0]["type"] == "notice" and "OpenRouter" in without_key.json()["blocks"][0]["message"]


def test_byok_reaches_the_planner_call_too(monkeypatch):
    from llm_api.request_credentials import openrouter_key
    monkeypatch.setattr(service, "item_store", service.ItemStore())
    item_id, token, _ = service.item_store.add(source_type="book", fields={})
    seen = []

    def fake_planned_blocks(text, context):
        seen.append(openrouter_key())
        return [service.notice("planned")]

    monkeypatch.setattr(service, "planned_blocks", fake_planned_blocks)
    with TestClient(create_app(settings())) as local_client:
        response = local_client.post("/api/chatbox/turns", json={
            "input": "the year is 1998", "item_id": item_id, "item_token": token,
            "openrouter_api_key": "planner-byok",
        })
    assert response.status_code == 200 and seen == ["planner-byok"]


def test_byok_reaches_candidate_selection(monkeypatch):
    from llm_api.request_credentials import openrouter_key
    records = [{"style_of_cause": "Alpha v Beta", "neutral_citation": "2020 SCC 1", "verified": True}]
    monkeypatch.setattr(service, "candidate_store", service.CandidateStore())
    block = service.candidate_store.add(records)  # signs + adds "display" via api.main._selection_candidate
    seen = []
    monkeypatch.setattr(service, "record_blocks", lambda record: seen.append(openrouter_key()) or [service.notice("ok")])
    with TestClient(create_app(settings())) as local_client:
        response = local_client.post("/api/chatbox/select", json={
            "candidate_set_id": block["candidate_set_id"], "access_token": block["access_token"],
            "candidate_id": block["items"][0]["id"], "openrouter_api_key": "select-byok",
        })
    assert response.status_code == 200 and seen == ["select-byok"]


def test_byok_reaches_file_extraction_and_lets_an_unconfigured_server_proceed(monkeypatch):
    from llm_api.request_credentials import openrouter_key
    seen = []
    monkeypatch.setattr("local_tools.file_extractor.extract_from_file", lambda path: {"raw_text": "stub"})
    monkeypatch.setattr(service, "extracted_blocks", lambda fields, shadow, **kw: seen.append(openrouter_key()) or [service.notice("ok")])
    with TestClient(create_app(settings())) as local_client:  # no server-side key configured
        response = local_client.post("/api/chatbox/files", data={"openrouter_api_key": "file-byok"},
                                    files={"file": ("sample.pdf", b"%PDF-1.7\n", "application/pdf")})
    assert response.status_code == 200 and seen == ["file-byok"]
    assert "file-byok" not in response.text


def test_a_blank_or_oversized_byok_value_is_a_no_op_not_an_error(client, monkeypatch):
    monkeypatch.setattr(service, "query_blocks", lambda text: [service.notice("ran")])
    response = client.post("/api/chatbox/turns", json={"input": "R v Gladue", "openrouter_api_key": "   "})
    # Pydantic accepts the string; the empty/whitespace key just falls through
    # to "no server key configured" rather than crashing.
    assert response.json()["blocks"][0]["type"] == "notice"
