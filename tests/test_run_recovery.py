"""Interrupted calls and simultaneous restoration keep a resumable workspace."""

import threading

import pytest
from pydantic import BaseModel

from core.tool_contracts import Field, Record
from harness import llm
from harness.core import Harness
from harness.plugin import Plugin, Result, Tool
from harness.session import SessionStore


class EmptyParams(BaseModel):
    pass


def _requests():
    return [{"id": name, "type": "function", "function": {"name": "work__save", "arguments": "{}"}}
            for name in ("first", "second")]


def _assert_closed(history):
    requested = [call["id"] for message in history for call in message.get("tool_calls", [])]
    answered = [message["tool_call_id"] for message in history if message.get("role") == "tool"]
    assert requested and sorted(requested) == sorted(answered)


def _harness(tmp_path, handler):
    plugin = Plugin("work", "Work", "Offline recovery fixture.", tools=[
        Tool("save", "Save a result.", EmptyParams, handler)], default_enabled=True)
    h = Harness({"work": plugin}, model="offline", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    session.loaded = ["work"]
    return h, session


def test_cancelled_tool_batch_closes_calls_and_preserves_saved_result(tmp_path, monkeypatch):
    executions = []

    def save(ctx, params):
        executions.append("saved")
        ref = ctx.records.put(Record("document", {"text": Field("Saved before stop.", "extracted")},
                                     "file", "attachment"))
        ctx.session.cancelled.set()
        return Result({"ref": ref})

    h, session = _harness(tmp_path, save)

    def stream(*args, **kwargs):
        yield "done", {"content": "", "tool_calls": _requests()}

    monkeypatch.setattr(llm, "chat_stream", stream)
    with pytest.raises(llm.LLMError, match="stopped"):
        h.run_turn(session, "Start research")
    assert executions == ["saved"]
    assert session.records.get_exact("rec_1").fields["text"].value == "Saved before stop."
    h._open_turn(session, "Continue the saved work", [])
    _assert_closed(h._history(session))


def test_closing_stream_between_activity_and_receipt_closes_tool_batch(tmp_path, monkeypatch):
    executions = []

    def save(ctx, params):
        executions.append("saved")
        return Result({"ok": True}, [{"type": "notice", "text": "Saved."}])

    h, session = _harness(tmp_path, save)

    def stream(*args, **kwargs):
        yield "done", {"content": "", "tool_calls": _requests()}

    monkeypatch.setattr(llm, "chat_stream", stream)
    events = h.run_turn_stream(session, "Start research")
    assert next(events)[0] == "block"  # activity precedes the model-visible receipt
    events.close()
    assert executions == ["saved"]
    h._open_turn(session, "Continue the saved work", [])
    _assert_closed(h._history(session))


def test_simultaneous_restore_returns_one_session_and_one_lock(tmp_path, monkeypatch):
    initial = SessionStore(store_dir=tmp_path / "sessions")
    original, token = initial.start()
    store = SessionStore(store_dir=tmp_path / "sessions")
    revive = store._revive
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    revived_calls = []

    def slow_revive(*args):
        revived_calls.append(1)
        entered.set()
        assert release.wait(3)
        return revive(*args)

    monkeypatch.setattr(store, "_revive", slow_revive)
    results, errors = [], []

    def restore(second=False):
        try:
            if second:
                second_started.set()
            results.append(store.get(original.id, token))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=restore)
    second = threading.Thread(target=restore, args=(True,))
    first.start()
    try:
        assert entered.wait(3)
        second.start()
        assert second_started.wait(3)
        # Give both callers a chance to reach restoration. The first remains
        # held above; a correct store serializes the second behind its lock.
        second.join(0.05)
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not errors and not first.is_alive() and not second.is_alive()
    assert len(results) == 2 and results[0] is not None
    assert results[0] is results[1] and results[0].lock is results[1].lock
    assert len(revived_calls) == 1


def test_delete_racing_restore_cannot_return_live_deleted_workspace(tmp_path, monkeypatch):
    initial = SessionStore(store_dir=tmp_path / "sessions")
    original, token = initial.start()
    store = SessionStore(store_dir=tmp_path / "sessions")
    revive = store._revive
    read, release, deleted = threading.Event(), threading.Event(), threading.Event()

    def slow_revive(*args):
        result = revive(*args)
        read.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(store, "_revive", slow_revive)
    results, errors = [], []

    def restore():
        try:
            results.append(store.get(original.id, token))
        except BaseException as exc:
            errors.append(exc)

    def delete():
        store.delete(original.id)
        deleted.set()

    reader = threading.Thread(target=restore)
    remover = threading.Thread(target=delete)
    reader.start()
    try:
        assert read.wait(3)
        remover.start()
        deleted.wait(0.05)
    finally:
        release.set()
        reader.join(3)
        if remover.ident is not None:
            remover.join(3)
    assert not errors and deleted.is_set()
    assert results and (results[0] is None or results[0].deleted)
    assert store.get(original.id, token) is None
    assert not (store.store_dir / f"{original.id}.json").exists()
