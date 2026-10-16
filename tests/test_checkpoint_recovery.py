"""Completed work survives interruption without misleading saved-state claims."""
import json

import pytest

from core.tool_contracts import Field, Record
from harness import llm
from harness.core import Harness, _model_content
from harness.plugin import Plugin, Result, Tool
from harness.session import SessionStore
from pydantic import BaseModel


class Empty(BaseModel):
    pass


def test_completed_tool_is_checkpointed_before_first_ui_event(tmp_path, monkeypatch):
    def save(ctx, _):
        ref = ctx.records.put(Record("document", {"text": Field("Completed source.", "extracted")}, "file", "test"))
        return Result({"ref": ref}, [{"type": "notice", "text": "Saved source."}])

    plugin = Plugin("work", "Work", "Offline fixture", tools=[Tool("save", "Save", Empty, save)], default_enabled=True)
    harness = Harness({"work": plugin}, config_path=tmp_path / "h.json")
    session, token = harness.sessions.start()
    session.loaded.append("work")
    call = {"id": "one", "type": "function", "function": {"name": "work__save", "arguments": "{}"}}

    def stream(*_, **kwargs):
        yield "done", {"content": "", "tool_calls": [call]}

    monkeypatch.setattr(llm, "chat_stream", stream)
    events = harness.run_turn_stream(session, "Save the source")
    assert next(events)[0] == "block"
    # A separate process can now restore completed work, even before this
    # generator's finalizer saves the end-of-turn snapshot.
    restored = SessionStore(store_dir=tmp_path / "sessions").get(session.id, token)
    assert restored.records.get_exact("rec_1").fields["text"].value == "Completed source."
    assert restored.messages[-1]["tool_call_id"] == "one"
    events.close()


def test_revive_closes_unfinished_tool_batch(tmp_path):
    store = SessionStore(store_dir=tmp_path / "sessions")
    session, token = store.start()
    session.messages = [{"role": "user", "content": "Continue"}, {"role": "assistant", "tool_calls": [
        {"id": "unfinished", "function": {"name": "work__save", "arguments": "{}"}}]}]
    store.save(session)
    restored = SessionStore(store_dir=tmp_path / "sessions").get(session.id, token)
    assert restored.messages[-1]["tool_call_id"] == "unfinished"
    assert json.loads(restored.messages[-1]["content"])["kind"] == "interrupted"


def test_disk_failure_is_visible_and_retry_clears_warning(tmp_path, monkeypatch):
    store = SessionStore(store_dir=tmp_path / "sessions")
    session, _ = store.start()
    with monkeypatch.context() as patch:
        import sqlite3
        session.messages.append({"role": "user", "content": "unsaved"})
        patch.setattr(store, "_write", lambda *_, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
        store.save(session)
        assert "could not be saved to disk" in session.persistence_error
    store.save(session)
    assert not session.persistence_error


def test_large_tool_content_keeps_critical_state_and_real_size_bound():
    report = {"object": {"text": '\\"\n' * 12000}, "stale": True, "needs_review": True,
              "review_status": "review_invalidated", "mechanical_binding": "intact",
              "issues": [{"code": "dependency_revised", "ref": "rec_7"}], "ref": "art_5"}
    rendered = _model_content(report)
    assert len(rendered) <= 6000
    summary = json.loads(rendered)
    assert summary["stale"] and summary["needs_review"]
    assert summary["review_status"] == "review_invalidated"
    assert summary["issues"][0]["code"] == "dependency_revised"


def test_untrusted_material_cannot_create_unbounded_ref_preview():
    rendered = _model_content({"text": "rec_" + "1" * 10000})
    assert len(rendered) <= 6000
    assert json.loads(rendered)["stored_refs"] == []


def test_readonly_ui_refresh_does_not_expand_conversation(tmp_path):
    plugin = Plugin("work", "Work", "Fixture", actions={"list": lambda *_, **kwargs: Result({"items": []}, [{"type": "notice"}])},
                    default_enabled=True, readonly_actions=("list",))
    harness = Harness({"work": plugin}, config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    for _ in range(3):
        assert harness.run_action(session, "work", "list", {})
    assert session.messages == [] and session.timeline == [] and session.loaded == []


def test_queued_generation_stops_but_next_explicit_request_can_continue(tmp_path, monkeypatch):
    from harness.app import _stream_locked
    harness = Harness({}, config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    queued_generation = session.cancel_generation
    session.cancel()
    monkeypatch.setattr(llm, "chat_stream", lambda *_, **kwargs: iter([("done", {"content": "Continued."})]))
    with pytest.raises(llm.LLMError, match="queued run was stopped"):
        harness.run_turn(session, "Old queued work", request_generation=queued_generation)
    # A new explicit request after Stop uses the new generation.
    blocks = list(_stream_locked(session, harness.run_turn_stream, session, "Continue", []))
    assert any(kind == "block" and payload.get("text") == "Continued." for kind, payload in blocks)


def test_opened_method_note_survives_history_trimming(tmp_path):
    harness = Harness({}, config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    skill = harness.skills["research_review"]
    session.read_skills.append(skill.name)
    session.messages = [{"role": "user", "content": "Use the research method"},
                        {"role": "tool", "content": json.dumps({"content": skill.body})}]
    for index in range(30):
        session.messages.extend([{"role": "user", "content": f"Next {index}"}, {"role": "assistant", "content": "Ready"}])
    assert skill.body not in str(harness._history(session))
    assert skill.body in harness._system_prompt(session)
