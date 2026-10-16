"""Answer reviews may use their own model; blank keeps the main model."""
import time

from fastapi.testclient import TestClient

from harness import llm
from harness.app import create_app
from harness.core import Harness
from harness.runtime import TurnBudget


def running(h):
    session, _ = h.sessions.start()
    session.running = True
    session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    return session


def review_with(h, monkeypatch):
    used = []
    monkeypatch.setattr(llm, "chat", lambda model, *a, **k: used.append(model) or {"content": "{}"})
    message, audit = h.request_review(running(h), [{"role": "user", "content": "check"}], [])
    return used, audit


def test_blank_review_model_uses_the_main_model(tmp_path, monkeypatch):
    h = Harness({}, model="main/model", config_path=tmp_path / "h.json")
    used, audit = review_with(h, monkeypatch)
    assert used == ["main/model"] and audit["model"] == "main/model"


def test_review_model_is_used_for_reviews_and_persisted(tmp_path, monkeypatch):
    h = Harness({}, model="main/model", config_path=tmp_path / "h.json")
    h.set_review_model(" strong/reviewer ")
    used, audit = review_with(h, monkeypatch)
    assert used == ["strong/reviewer"] and audit["model"] == "strong/reviewer"
    reloaded = Harness({}, model="main/model", config_path=tmp_path / "h.json")
    assert reloaded.review_model == "strong/reviewer" and reloaded.config["model"] == "main/model"
    reloaded.set_review_model("")
    assert reloaded.review_model == "main/model"


def test_settings_endpoint_sets_and_clears_review_model(tmp_path):
    h = Harness({}, model="main/model", config_path=tmp_path / "h.json")
    with TestClient(create_app(h)) as client:
        body = client.post("/api/settings/review-model", json={"model": "strong/reviewer"}).json()
        assert body["ok"] and body["review_model"] == "strong/reviewer" and body["model"] == "main/model"
        assert client.post("/api/settings/review-model", json={"model": ""}).json()["review_model"] == ""
        assert client.post("/api/settings/review-model", json={"model": "has space"}).status_code == 422
