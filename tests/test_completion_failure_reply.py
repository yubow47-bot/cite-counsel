"""Failed completion delivers a plugin's safe fallback without another loop."""
import time
from dataclasses import replace

import pytest
from pydantic import BaseModel

from harness import llm
from harness.core import Harness
from harness.plugin import CompletionRequest, Plugin, Result, Tool
from harness.runtime import TurnBudget


SAFE = "The supplied figures show a 20% decline. Whether the policy caused it remains unverified."
DRAFT = "The policy definitely caused the entire decline."


class Params(BaseModel):
    pass


@pytest.fixture
def work(tmp_path, monkeypatch):
    plugin = Plugin("checked", "Checked completion", "A completion fixture.", default_enabled=True,
        tools=[Tool("finish", "Save a completion", Params, lambda *_: Result(reply="Checked answer."))],
        prepare_completion=lambda *_: CompletionRequest([], "checked__finish", failure_reply=SAFE))
    harness = Harness({"checked": plugin}, model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    session.running = True
    session.runtime = TurnBudget(time.monotonic() + 90, 18, 10, 0)
    monkeypatch.setattr(harness.sessions, "save", lambda *_: None)
    monkeypatch.setattr(harness.sessions, "append_event", lambda *_: None)
    calls = []

    def review(*args, **kwargs):
        calls.append(kwargs.get("mode"))
        return {"tool_calls": [{"id": "requested", "function": {
            "name": "checked__finish", "arguments": "{}"}}]}, {"status": "model_reviewed"}

    monkeypatch.setattr(harness, "request_review", review)
    return harness, session, calls


@pytest.mark.parametrize("reason", ["budget", "unavailable", "missing_tool", "missing_call", "wrong_call", "multiple_calls", "tool_error", "no_reply", "review_exception"])
def test_completion_failure_uses_safe_reply_once(work, monkeypatch, reason):
    harness, session, calls = work
    if reason == "budget":
        session.runtime.tool_count = session.runtime.max_calls
    elif reason == "unavailable":
        harness.plugins["checked"].tools[0].available = lambda *_: False
    elif reason == "missing_tool":
        harness.plugins["checked"].prepare_completion = lambda *_: CompletionRequest([], "checked__missing", SAFE)
    elif reason in {"missing_call", "wrong_call", "multiple_calls"}:
        def invalid_review(*args, **kwargs):
            calls.append("invalid")
            call = {"id": "invalid", "function": {"name": "other__finish", "arguments": "{}"}}
            return {"tool_calls": [] if reason == "missing_call" else [call] * (2 if reason == "multiple_calls" else 1)}, {}
        monkeypatch.setattr(harness, "request_review", invalid_review)
    elif reason == "tool_error":
        harness.plugins["checked"].tools[0].handler = lambda *_: Result({"error": "Invalid completion."})
    elif reason == "no_reply":
        harness.plugins["checked"].tools[0].handler = lambda *_: Result({"needs_sources": True})
    else:
        def failed_review(*args, **kwargs):
            calls.append("failed")
            raise RuntimeError("review failed")
        monkeypatch.setattr(harness, "request_review", failed_review)
    blocks = []
    assert harness._complete_reply(session, DRAFT, blocks, {}) == SAFE
    assert any(block["type"] == "notice" for block in blocks)
    assert len(calls) <= 1 and session.runtime.tool_count <= session.runtime.max_calls
    assert session.runtime.reviewed_call_id is None


def test_success_delivers_the_checked_answer_not_the_failure_reply(work):
    harness, session, calls = work
    assert harness._complete_reply(session, DRAFT, [], {}) == "Checked answer."
    assert calls == ["final_completion"] and session.runtime.tool_count == 1


def test_plugins_without_fallback_keep_the_existing_behavior(work, monkeypatch):
    harness, session, _ = work
    harness.plugins["checked"] = replace(harness.plugins["checked"],
        prepare_completion=lambda *_: CompletionRequest([], "checked__finish"))
    monkeypatch.setattr(harness, "request_review", lambda *_, **__: (None, {}))
    assert harness._complete_reply(session, DRAFT, [], {}) == DRAFT


def test_fallback_does_not_turn_cancellation_into_delivery(work, monkeypatch):
    harness, session, _ = work
    def cancelled(*_, **__):
        raise llm.LLMCancelled("Stopped")
    monkeypatch.setattr(harness, "request_review", cancelled)
    with pytest.raises(llm.LLMCancelled):
        harness._complete_reply(session, DRAFT, [], {})
    assert not session.messages


def test_final_delivery_and_history_use_safe_text_after_completion_failure(work, monkeypatch):
    harness, session, _ = work
    monkeypatch.setattr(harness, "request_review", lambda *_, **__: (None, {}))
    blocks = []
    harness._deliver_reply(session, DRAFT, blocks, {})
    assert session.messages[-1] == {"role": "assistant", "content": SAFE}
    assert blocks[-1]["text"] == SAFE
    assert all(block.get("text") != DRAFT for block in blocks)
