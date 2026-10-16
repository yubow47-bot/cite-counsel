"""Answer reviews may use their own model; blank keeps the main model."""
import time
import json

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


def test_decision_wrap_up_and_final_review_use_their_selected_models(tmp_path, monkeypatch):
    h = Harness({}, model="default/model", config_path=tmp_path / "h.json")
    h.set_models({"decide": "decision/model", "review": "review/model", "completion": "wrap/model"})
    used = []

    def stream(model, *args, **kwargs):
        used.append(model)
        yield "done", {"role": "assistant", "content": f"Reply from {model}"}

    monkeypatch.setattr(llm, "chat_stream", stream)
    session, _ = h.sessions.start()
    assert h.run_turn(session, "Ordinary reply")[-1]["text"] == "Reply from decision/model"
    monkeypatch.setattr("harness.core.MAX_STEPS", 0)
    assert h.run_turn(session, "Wrap up")[-1]["text"] == "Reply from wrap/model"
    monkeypatch.setattr(llm, "chat", lambda model, *a, **k: used.append(model) or {"content": "{}"})
    _, audit = h.request_review(running(h), [], [], mode="final_completion")
    assert used == ["decision/model", "wrap/model", "review/model"]
    assert audit["model"] == "review/model"
    events = [json.loads(line) for line in
              (h.sessions.store_dir / f"{session.id}.events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["payload"]["model"] for row in events if row["kind"] == "model_request"] == used[:2]


def test_stage_settings_preserve_legacy_reviews_and_blank_falls_back(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"model": "default/model", "review_model": "legacy/reviewer"}), encoding="utf-8")
    h = Harness({}, config_path=path)
    with TestClient(create_app(h)) as client:
        updated = client.post("/api/settings/models", json={"decide": "decision/model"}).json()
        assert updated["models"]["review"] == "legacy/reviewer"
        assert client.post("/api/settings/models", json={"completion": "has space"}).status_code == 422
    reloaded = Harness({}, config_path=path)
    assert reloaded.model_for("decide") == "decision/model"
    assert reloaded.model_for("completion") == "default/model"
    reloaded.set_models({"review": ""})
    assert reloaded.review_model == "default/model" and reloaded.config["review_model"] == ""
