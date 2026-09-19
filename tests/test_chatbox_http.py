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
        assert "2020 SCC 1" in result["citation"] and result["verified"] is False
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
    assert "Original source text" in blocks[-1]["message"]
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
