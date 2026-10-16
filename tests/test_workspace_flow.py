"""Workspace regressions: restored evidence, honest interruption, bounded runs."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from core.tool_contracts import Artifact, Derivation, Field, Record
from harness import llm
from harness.app import _stream_locked, create_app
from harness.core import Harness, MAX_CALLS_PER_ROUND
from harness.plugin import Plugin, Result, Tool
from harness.session import SessionStore


def make(tmp_path):
    return Harness({}, model="offline", config_path=tmp_path / "harness.json")


def record(text):
    return Record("document", {"title": Field("First matter", "extracted"),
                               "text": Field(text, "extracted")}, "file", "att_example")


def test_versions_and_directory_survive_restart(tmp_path):
    h = make(tmp_path)
    session, token = h.sessions.start()
    old = session.records.put(record("Original evidence"))
    new = session.records.put(record("Corrected evidence"), supersedes=old)
    newer = session.records.put(record("Final evidence"), supersedes=old)
    first_hash = session.records.fingerprint(old)
    h.sessions.save(session)
    restored = make(tmp_path).sessions.get(session.id, token)
    assert restored.records.get(old).fields["text"].value == "Final evidence"
    assert restored.records.current_ref(new) == newer
    assert restored.records.get_exact(old).fields["text"].value == "Original evidence"
    assert restored.records.fingerprint(old) == first_hash
    assert [item["ref"] for item in restored.records.catalog()["items"]] == [newer]
    assert restored.records.catalog(query="First matter")["total"] == 1


def test_old_material_is_discoverable_after_history_clipping(tmp_path):
    h = make(tmp_path)
    session, _ = h.sessions.start()
    ref = session.records.put(record("Evidence far beyond the chat history"))
    for i in range(30):
        session.messages += [{"role": "user", "content": f"Question {i}"},
                             {"role": "assistant", "content": "Answer"}]
    assert "First matter" not in json.dumps(h._history(session))
    assert ref in h._system_prompt(session) and "First matter" in h._system_prompt(session)
    _, result = h._execute(session, {"function": {"name": "record__list", "arguments": '{"query":"First matter"}'}})
    assert result.content["items"][0]["ref"] == ref


def test_resume_restores_cards_and_paged_material_with_auth(tmp_path, monkeypatch):
    h = make(tmp_path)
    monkeypatch.setattr(llm, "chat_stream", lambda *a, **k: iter([("done", {"content": "Saved reply"})]))
    with TestClient(create_app(h)) as client:
        response = client.post("/api/turns", json={"input": "Read my material"}).json()
        credentials = {"session_id": response["session"]["id"], "session_token": response["session"]["token"]}
        session = h.sessions.get(credentials["session_id"], credentials["session_token"])
        ref = session.records.put(record("a" * 5000 + "late evidence"))
        h.sessions.save(session)
        restored = client.post("/api/sessions/resume", json=credentials).json()
        assert restored["timeline"][0]["text"] == "Read my material"
        assert restored["timeline"][1]["blocks"][-1]["text"] == "Saved reply"
        page = client.post(f"/api/materials/{ref}", json={**credentials, "offset": 4000}).json()
        assert page["text"].endswith("late evidence") and page["total_chars"] == 5013
        assert client.post("/api/materials", json={**credentials, "session_token": "wrong"}).status_code == 404
    with TestClient(create_app(make(tmp_path))) as client:
        assert client.post("/api/sessions/resume", json=credentials).json()["timeline"] == restored["timeline"]


def test_saved_artifacts_are_readable_without_model_history(tmp_path):
    h = make(tmp_path)
    session, _ = h.sessions.start()
    ref = session.records.put(Artifact("draft", "A saved draft", Derivation((Field("draft", "model"),), "draft.v1")))
    _, result = h._execute(session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": ref, "field": "content"})}})
    assert result.content["text"] == "A saved draft"


def test_search_locates_late_passages_on_exact_versions(tmp_path):
    h = make(tmp_path)
    session, _ = h.sessions.start()
    old = session.records.put(record("background " * 9000 + "Decisive\nlate evidence"))
    latest = session.records.put(record("Replacement text"), supersedes=old)
    call = {"function": {"name": "record__search", "arguments": json.dumps({"ref": old, "query": "decisive late evidence"})}}
    _, result = h._execute(session, call)
    assert result.content["matches"][0]["start"] > 90000
    assert result.content["matches"][0]["ref"] == old
    call["function"]["arguments"] = json.dumps({"query": "decisive late evidence"})
    assert h._execute(session, call)[1].content["matches"] == []


def test_deleted_worker_cannot_recreate_a_session_or_log(tmp_path):
    store = SessionStore(store_dir=tmp_path)
    session, token = store.start()
    session.timeline.append({"role": "user", "text": "saved"})
    store.delete(session.id)
    store.save(session)
    store.append_event(session, "late_response", {"content": "late"})
    assert session.cancelled.is_set() and session.deleted
    assert not list(tmp_path.glob(session.id + "*"))
    assert store.get(session.id, token) is None


def test_expired_session_is_not_revived_in_same_process(tmp_path):
    store = SessionStore(store_dir=tmp_path)
    session, token = store.start()
    from session_db import set_wall_expires
    set_wall_expires(store, session.id, 0)
    session.expires = 0
    assert store.get(session.id, token) is None


def test_disconnecting_bridge_signals_cancel_and_does_not_resurrect(tmp_path):
    store = SessionStore(store_dir=tmp_path)
    session, token = store.start()
    release, finished = threading.Event(), threading.Event()

    def work():
        try:
            yield "thinking_delta", "starting"
            release.wait(2)
            yield "block", {"type": "text", "text": "late"}
        finally:
            store.save(session)
            finished.set()

    bridge = _stream_locked(session, work)
    assert next(bridge)[0] == "thinking_delta"
    bridge.close()
    assert session.cancelled.is_set()
    store.delete(session.id)
    release.set()
    assert finished.wait(2)
    assert store.get(session.id, token) is None


@pytest.mark.parametrize("ending", ["error", "eof", "length"])
def test_partial_provider_stream_never_commits_or_executes_tools(tmp_path, monkeypatch, ending):
    h = make(tmp_path)
    session, _ = h.sessions.start()

    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def close(self): pass
        def iter_lines(self, **kwargs):
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"x","function":{"name":"record__compose","arguments":"{}"}}]}}]}'
            if ending == "error":
                yield 'data: {"error":{"code":500,"message":"failed"}}'
            if ending == "length":
                yield 'data: {"choices":[{"delta":{},"finish_reason":"length"}]}'
                yield 'data: [DONE]'

    monkeypatch.setattr(llm, "_post", lambda *a, **k: Response())
    with pytest.raises(llm.LLMError):
        h.run_turn(session, "Do work")
    assert not session.records.all()
    assert not any(message.get("tool_calls") for message in session.messages)
    assert session.timeline[-1]["blocks"][-1]["type"] == "notice"


def test_a_large_tool_batch_is_rejected_before_any_handler(tmp_path, monkeypatch):
    h = make(tmp_path)
    session, _ = h.sessions.start()
    calls = [{"id": str(i), "function": {"name": "record__compose", "arguments": "{}"}}
             for i in range(MAX_CALLS_PER_ROUND + 1)]
    monkeypatch.setattr(llm, "chat_stream", lambda *a, **k: iter([("done", {"tool_calls": calls})]))
    with pytest.raises(llm.LLMError, match="none of this batch"):
        h.run_turn(session, "Do work")
    assert not session.records.all()


def test_ui_action_context_and_cards_are_saved_after_the_action(tmp_path):
    plugin = Plugin("demo", "Demo", "Demo action.", default_enabled=True,
                    actions={"mark": lambda ctx, payload: Result({"marked": True}, [{"type": "text", "text": "Marked"}])})
    h = Harness({"demo": plugin}, model="offline", config_path=tmp_path / "harness.json")
    session, token = h.sessions.start()
    h.run_action(session, "demo", "mark", {})
    restored = make(tmp_path).sessions.get(session.id, token)
    assert restored.loaded == ["demo"]
    assert restored.messages[-1]["_note"] is True
    assert restored.timeline[-1]["blocks"][0]["text"] == "Marked"
