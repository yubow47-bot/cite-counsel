"""Sessions survive a restart: refs stay valid, numbering continues."""
import json
from unittest.mock import patch

from core.tool_contracts import Artifact, Derivation, Field, Finding, Record, decode, encode
from harness.core import Harness
from harness.plugin import discover

QUOTE = "Parliament created the conditional sentencing regime in 1996."
TEXT = ("R. v. Sharma\n[1] Conditional sentences are a form of punishment.\n"
        "[2] Parliament created the conditional sentencing regime in 1996.\n")
URL = "https://decisions.scc-csc.ca/x"


def test_contract_objects_round_trip():
    calc = Derivation((Field("2026-01-01", "database", source_id="a2aj:c1"),), "limit:v1", ("start",))
    computed = Field("2026-01-31", "computed", rule_id="limit:v1", derivation=calc)
    record = Record("jurisprudence", {"style_of_cause": Field("R v X", "database", source_id="a2aj:c1"),
                                      "date": computed}, "a2aj", "c1")
    artifact = Artifact("binary", b"\x89PNG", Derivation((Field("x", "user"),), "render:v1"))
    finding = Finding("confirmed", "appears verbatim", "complete", calc)
    for obj in (record, artifact, finding):
        assert decode(encode(obj)) == obj


def run_one_turn(h, session, text):
    replies = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "a2aj__find_case",
                                                      "arguments": json.dumps({"query": text})}}]},
        {"role": "assistant", "content": "done"}]
    return replies


def test_session_survives_a_restart_with_its_refs(tmp_path, monkeypatch):
    config = tmp_path / "harness.json"
    h = Harness(discover(), model="m", config_path=config)
    session, token = h.sessions.start()
    session.loaded.append("a2aj")
    with patch("core.source_tools.search_cases_multi", return_value=[{
        "id": "case-17", "name_en": "Example v Example", "citation_en": "2024 SCC 1", "verified": True}]):
        h._execute(session, {"id": "c1", "type": "function", "function": {
            "name": "a2aj__find_case", "arguments": json.dumps({"query": "Example"})}})
    session.messages.append({"role": "assistant", "content": "found it"})
    h.sessions.save(session)

    # A fresh process: a new Harness over the same directory.
    revived_harness = Harness(discover(), model="m", config_path=config)
    revived = revived_harness.sessions.get(session.id, token)
    assert revived is not None
    assert "a2aj" in revived.loaded
    assert revived.messages[-1]["content"] == "found it"
    record = revived.records.get("rec_1", Record)
    assert record.fields["style_of_cause"].value == "Example v Example"
    assert record.fields["style_of_cause"].source_id == "a2aj:case-17"
    # Numbering continues from the file, so no ref can be reissued.
    ref = revived.records.put(Record("book", {}, "user", "blank"))
    assert ref == "rec_2"


def test_a_wrong_token_cannot_revive_a_session(tmp_path):
    config = tmp_path / "harness.json"
    h = Harness(discover(), model="m", config_path=config)
    session, token = h.sessions.start()
    h.sessions.save(session)
    other = Harness(discover(), model="m", config_path=config)
    assert other.sessions.get(session.id, "wrong-token") is None
    assert other.sessions.get(session.id, token) is not None


def test_deleting_a_session_removes_it_from_memory_and_disk(tmp_path):
    """No chat-history feature exists yet: "New chat" deletes the old
    session outright (SessionStore.delete) so nothing in it can resurface
    in what the user sees as a fresh conversation."""
    config = tmp_path / "harness.json"
    h = Harness(discover(), model="m", config_path=config)
    session, token = h.sessions.start()
    session.messages.append({"role": "user", "content": "some prior research"})
    h.sessions.save(session)
    on_disk = h.sessions.store_dir / f"{session.id}.json"
    assert on_disk.exists()

    h.sessions.delete(session.id)

    assert not on_disk.exists()
    assert h.sessions.get(session.id, token) is None    # gone from memory
    other = Harness(discover(), model="m", config_path=config)
    assert other.sessions.get(session.id, token) is None   # and cannot be revived from disk either


def test_expired_sessions_are_swept_from_disk(tmp_path):
    config = tmp_path / "harness.json"
    h = Harness(discover(), model="m", config_path=config)
    session, token = h.sessions.start()
    h.sessions.save(session)
    # Age the session past its wall-clock expiry (the sweep reads the file).
    import json as _json
    old = h.sessions.store_dir / f"{session.id}.json"
    payload = _json.loads(old.read_text(encoding="utf-8"))
    payload["wall_expires"] = 0
    old.write_text(_json.dumps(payload), encoding="utf-8")
    fresh = Harness(discover(), model="m", config_path=config)   # sweeps on init
    assert fresh.sessions.get(session.id, token) is None
    assert not old.exists()


def test_attachments_live_in_the_session_directory(tmp_path):
    config = tmp_path / "harness.json"
    h = Harness(discover(), model="m", config_path=config)
    session, token = h.sessions.start()
    directory = h.sessions.attachment_dir(session)
    path = directory / "att_x.pdf"
    path.write_bytes(b"%PDF-1.4")
    session.attachments["att_x"] = {"path": str(path), "name": "doc.pdf"}
    h.sessions.save(session)

    revived_harness = Harness(discover(), model="m", config_path=config)
    revived = revived_harness.sessions.get(session.id, token)
    assert revived.attachments["att_x"]["name"] == "doc.pdf"
    assert (revived_harness.sessions.store_dir / session.id / "attachments" / "att_x.pdf").is_file()


def test_the_quote_chain_survives_a_restart(tmp_path):
    """full text -> quote -> citation: every ref keeps meaning across a restart."""
    config = tmp_path / "harness.json"
    h = Harness(discover(), model="m", config_path=config)
    session, token = h.sessions.start()
    session.loaded.append("a2aj")
    with patch("core.source_tools.search_cases_multi", return_value=[{
        "id": "c9", "name_en": "R v Sharma", "citation_en": "2022 SCC 39", "verified": True}]):
        h._execute(session, {"id": "c1", "type": "function", "function": {
            "name": "a2aj__find_case", "arguments": json.dumps({"query": "Sharma"})}})
        h._execute(session, {"id": "c2", "type": "function", "function": {
            "name": "a2aj__full_text", "arguments": json.dumps({"ref": "rec_1"})}})
    h.sessions.save(session)

    revived = Harness(discover(), model="m", config_path=config)
    session = revived.sessions.get(session.id, token)
    session.messages.append({"role": "user", "content": f"核对：{QUOTE}"})
    session.loaded.append("quote")
    result = revived._execute(session, {"id": "c3", "type": "function", "function": {
        "name": "quote__check", "arguments": json.dumps({"ref": "rec_2", "quote": QUOTE})}})[1]
    assert result.content["verdict"] == "exact"
    # The full text from before the restart still backs the Finding's provenance.
    assert result.content["pinpoint_ref"]
