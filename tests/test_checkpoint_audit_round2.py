"""Independent interruption and bounded-summary regressions from second audit."""

import json
import subprocess
import sys
import threading
from pathlib import Path

from harness import app, llm
from harness.core import Harness
from harness.session import SessionStore


def test_model_summary_terminates_for_reference_shaped_document_text():
    # A malformed ref can come from any retrieved document. Isolate this
    # boundary check so an unbounded summarizer cannot hang the test worker.
    script = "from harness.core import _model_content; print(len(_model_content({'text': 'rec_' + '1' * 10000})))"
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=5, check=True)
    assert int(result.stdout.strip()) <= 6000


def test_cancelled_queued_stream_cannot_start_a_model_request(tmp_path, monkeypatch):
    h = Harness({}, model="offline", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    queued, release = threading.Event(), threading.Event()

    class SlotGate:
        def __enter__(self):
            queued.set()
            assert release.wait(3)

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(app, "_STREAM_SLOTS", SlotGate())
    model_calls = []

    def fake_model(*_, **kwargs):
        model_calls.append("called")
        yield "done", {"content": "Work should have been cancelled."}

    monkeypatch.setattr(llm, "chat_stream", fake_model)
    bridge = app._stream_locked(session, h.run_turn_stream, session, "Queued request", [])
    errors = []

    def consume():
        try:
            list(bridge)
        except Exception as exc:
            errors.append(exc)

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    assert queued.wait(3)
    session.cancelled.set()
    release.set()
    consumer.join(3)
    assert not consumer.is_alive()
    assert not model_calls


def test_user_request_is_checkpointed_before_first_reasoning_event(tmp_path, monkeypatch):
    h = Harness({}, model="offline", config_path=tmp_path / "h.json")
    session, token = h.sessions.start()

    def fake_model(*_, **kwargs):
        yield "reasoning", "Working on the question."
        yield "done", {"content": "Complete"}

    monkeypatch.setattr(llm, "chat_stream", fake_model)
    run = h.run_turn_stream(session, "An important new request", [])
    try:
        assert next(run)[0] == "thinking_delta"
        restored = SessionStore(store_dir=tmp_path / "sessions").get(session.id, token)
        assert any(message.get("content") == "An important new request" for message in restored.messages)
        assert "interrupted" in json.dumps(restored.timeline).lower()
    finally:
        run.close()
