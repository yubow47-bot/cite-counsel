"""The run log lays out what actually ran, per turn, from the audit log."""
import json

from fastapi.testclient import TestClient

from harness import llm
from harness.app import create_app
from harness.core import Harness
from harness.plugin import Plugin, Result, Tool
from harness.run_log import read_events, summarize
from pydantic import BaseModel


class Empty(BaseModel):
    pass


def scripted(monkeypatch, replies):
    queue = list(replies)

    def stream(*_, **__):
        yield "done", queue.pop(0)
    monkeypatch.setattr(llm, "chat_stream", stream)


def test_turns_rows_durations_and_budget(tmp_path, monkeypatch):
    plugin = Plugin("alpha", "Alpha", "Fixture", category="source", default_enabled=True,
                    tools=[Tool("echo", "Echo", Empty, lambda ctx, p: Result({"ok": True}))])
    h = Harness({"alpha": plugin}, model="main/model", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    session.loaded.append("alpha")
    call = {"id": "c1", "type": "function", "function": {"name": "alpha__echo", "arguments": "{}"}}
    scripted(monkeypatch, [{"role": "assistant", "content": None, "tool_calls": [call]},
                           {"role": "assistant", "content": "Done."},
                           {"role": "assistant", "content": "Second."}])
    h.run_turn(session, "First question")
    h.run_turn(session, "Second question")
    turns = summarize(read_events(h.sessions.store_dir / f"{session.id}.events.jsonl"))
    assert [t["text"] for t in turns] == ["First question", "Second question"] and all(t["marked"] for t in turns)
    kinds = [row["kind"] for row in turns[0]["rows"]]
    assert kinds[:4] == ["model_request", "model_response", "tool_call", "tool_result"] and kinds[-1] == "answer_delivered"
    result = next(row for row in turns[0]["rows"] if row["kind"] == "tool_result")
    assert result["tool"] == "alpha__echo" and result["receipt_status"] == "ok" and result["seconds"] >= 0
    request = turns[0]["rows"][0]
    assert request["model"] == "main/model" and request["phase"] == "investigation" and request["max_calls"] > 0


def test_old_logs_without_markers_or_state_report_unknowns(tmp_path):
    path = tmp_path / "old.events.jsonl"
    rows = [{"time": 10.0, "kind": "model_request", "payload": {"model": "m", "messages": [{}], "tools": []}},
            {"time": 11.0, "kind": "answer_delivered", "payload": {"text": "Hi"}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n{torn", encoding="utf-8")
    [turn] = summarize(read_events(path))
    assert not turn["marked"] and turn["text"] is None and turn["seconds"] == 1.0
    assert turn["rows"][0]["phase"] is None and turn["rows"][0]["remaining_s"] is None


def test_endpoint_requires_the_conversation_token(tmp_path, monkeypatch):
    h = Harness({}, model="m", config_path=tmp_path / "h.json")
    scripted(monkeypatch, [{"role": "assistant", "content": "Hello"}])
    with TestClient(create_app(h)) as client:
        started = client.post("/api/turns", json={"input": "Hi"}).json()["session"]
        body = client.post("/api/run-log", json={"session_id": started["id"], "session_token": started["token"]}).json()
        assert body["ok"] and body["turns"][-1]["text"] == "Hi"
        assert client.post("/api/run-log", json={"session_id": started["id"], "session_token": "wrong"}).status_code == 404
