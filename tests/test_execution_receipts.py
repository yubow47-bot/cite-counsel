"""Investigation records use actual execution evidence, not claimed searches."""
import json

from pydantic import BaseModel

from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.core import Harness, _model_content
from harness.plugin import Plugin, Result, Tool
from harness.session import SessionStore


class Empty(BaseModel):
    pass


def setup_harness(tmp_path, handler):
    plugin = Plugin("source", "Source", "Fixture", category="source", default_enabled=True,
                    tools=[Tool("search", "Search", Empty, handler)])
    h = Harness({"source": plugin}, config_path=tmp_path / "h.json")
    session, token = h.sessions.start()
    session.loaded.append("source")
    return h, session, token


def run(h, session):
    return h._execute_once(session, {"id": "test", "function": {"name": "source__search", "arguments": "{}"}}, {})[1]


def test_receipts_capture_actual_refs_and_ignore_reference_shaped_source_prose(tmp_path):
    def handler(ctx, _):
        a = ctx.records.put(Record("document", {"text": Field("Evidence", "database", source_id="test:a")}, "fixture", "a"))
        ctx.records.put(Record("document", {"text": Field("Unrelated", "database", source_id="test:b")}, "fixture", "b"))
        return Result({"ref": a, "text": "A document says rec_2, which is just source prose."})
    h, session, _ = setup_harness(tmp_path, handler)
    result = run(h, session)
    receipt = result.content["_execution_receipt"]
    assert receipt["category"] == "source" and receipt["status"] == "ok"
    assert receipt["refs"] == [{"ref": "rec_1", "fingerprint": session.records.fingerprint("rec_1")}]
    assert receipt["id"] == session.tool_receipts[0]["id"]


def test_empty_search_and_failed_search_have_different_receipts(tmp_path):
    h, session, _ = setup_harness(tmp_path, lambda *_, **kwargs: Result({"found": 0, "coverage": {"status": "complete"}}))
    assert run(h, session).content["_execution_receipt"]["status"] == "no_match"
    h.plugins["source"].tools[0].handler = lambda *_, **kwargs: Result({"error": "Timeout", "kind": "source_unavailable"})
    assert run(h, session).content["_execution_receipt"]["status"] == "error"


def test_receipts_survive_restart_and_long_result_preview(tmp_path):
    h, session, token = setup_harness(tmp_path, lambda *_, **kwargs: Result({"text": "Long material. " * 2000}))
    result = run(h, session)
    receipt_id = result.content["_execution_receipt"]["id"]
    assert json.loads(_model_content(result.content))["_execution_receipt"]["id"] == receipt_id
    h.sessions.save(session)
    revived = SessionStore(store_dir=tmp_path / "sessions").get(session.id, token)
    assert revived.tool_receipts[0]["id"] == receipt_id


def test_reading_a_generated_artifact_is_not_source_investigation(tmp_path):
    h = Harness({}, config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    ref = session.records.put(Artifact("memo", "Model reasoning", Derivation((Field("Model reasoning", "model"),), "fixture.v1")))
    _, result = h._execute_once(session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": ref, "field": "content"})}}, {})
    assert session.tool_receipts[-1]["category"] == "core"


def test_model_text_on_a_record_with_database_metadata_is_not_source_inspection(tmp_path):
    h = Harness({}, config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    ref = session.records.put(Record("document", {"title": Field("Database title", "database", source_id="test:x"),
                                                 "text": Field("Invented account", "model")}, "fixture", "x"))
    h._execute_once(session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": ref, "field": "text"})}}, {})
    assert session.tool_receipts[-1]["category"] == "core"


def test_search_observation_offsets_bind_the_returned_context_not_just_match(tmp_path):
    import hashlib
    h = Harness({}, config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    text = "Background " * 50 + "important passage" + " Ending" * 50
    ref = session.records.put(Record("document", {"text": Field(text, "extracted")}, "fixture", "x"))
    h._execute_once(session, {"function": {"name": "record__search", "arguments": json.dumps({"ref": ref, "query": "important passage"})}}, {})
    observed = session.tool_receipts[-1]["observations"][0]
    actual = text[observed["offset"]:observed["end"]]
    assert hashlib.sha256(actual.encode()).hexdigest() == observed["text_sha256"]
    assert text[observed["match_start"]:observed["match_end"]] == "important passage"


def test_saved_investigation_survives_expiry_and_disk_limit(tmp_path):
    store = SessionStore(store_dir=tmp_path / "sessions", limit=1)
    session, token = store.start()
    session.retained = True
    store.save(session)
    path = store.store_dir / (session.id + ".json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["wall_expires"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    store.start()
    revived = SessionStore(store_dir=tmp_path / "sessions", limit=1).get(session.id, token)
    assert revived is not None and revived.retained


def test_plugin_context_and_final_checks_are_visible_and_fail_honestly(tmp_path):
    from harness import llm
    plugin = Plugin("checker", "Checker", "Fixture", default_enabled=True,
                    context_provider=lambda ctx: "critical question remains open",
                    final_response_checks=lambda ctx, text: [{"type": "notice", "text": "Evidence is incomplete."}])
    h = Harness({"checker": plugin}, config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    assert "critical question remains open" in h._system_prompt(session)
    blocks = []
    h._add_caption(session, "Here is the answer", blocks)
    assert blocks[0]["plugin"] == "checker" and blocks[0]["text"] == "Evidence is incomplete."


def test_nearing_deadline_reserves_a_reply_instead_of_starting_more_research(tmp_path, monkeypatch):
    from harness import core, llm
    h = Harness({}, config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    elapsed = [0.0]
    requests = []
    monkeypatch.setattr(core.time, "monotonic", lambda: elapsed[0])

    def stream(model, messages, tools, **kwargs):
        requests.append((messages, tools))
        if len(requests) == 1:
            assert "Remaining execution budget" in messages[0]["content"]
            elapsed[0] = core.MAX_TURN_SECONDS - 20
            yield "done", {"content": "", "tool_calls": [{"id": "list", "function": {"name": "record__list", "arguments": "{}"}}]}
        else:
            assert tools == []
            yield "done", {"content": "A provisional answer, with the remaining uncertainty stated."}

    monkeypatch.setattr(llm, "chat_stream", stream)
    blocks = h.run_turn(session, "Investigate this question")
    assert len(requests) == 2
    assert any(block.get("type") == "text" and "provisional answer" in block["text"] for block in blocks)
