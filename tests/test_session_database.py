"""Snapshots in SQLite: one-time legacy import, incremental writes, rollback."""
import hashlib
import json
import sqlite3
import time

import pytest

from core.tool_contracts import Field, Record, encode
from harness import session as session_module
from harness.session import SessionStore
from session_db import exists, parts


def legacy_file(directory, session_id="legacyA", token="tok", *, messages=None, retained=False, expires=None):
    """A snapshot exactly as the JSON-file store used to write it."""
    record = Record("document", {"text": Field("Saved passage", "extracted")}, "fixture", "doc")
    payload = {"v": 1, "wall_expires": expires if expires is not None else time.time() + 3600,
               "token_hash": hashlib.sha256(token.encode()).hexdigest(),
               "messages": messages or [{"role": "user", "content": "Earlier question"}],
               "loaded": ["research"], "read_skills": [], "state": {"inquiry": {"active_ref": "rec_1"}},
               "attachments": [], "timeline": [{"role": "user", "text": "Earlier question"}],
               "running": False, "tool_receipts": [{"id": "r1", "status": "ok"}], "retained": retained,
               "records": [{"ref": "rec_1", "data": encode(record), "meta": {"note": "kept"}}]}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_legacy_snapshot_is_imported_once_and_kept_as_backup(tmp_path):
    path = legacy_file(tmp_path)
    store = SessionStore(store_dir=tmp_path)
    revived = store.get("legacyA", "tok")
    assert revived.messages == [{"role": "user", "content": "Earlier question"}]
    assert revived.state["inquiry"]["active_ref"] == "rec_1" and revived.tool_receipts[0]["id"] == "r1"
    assert revived.records.get_exact("rec_1").fields["text"].value == "Saved passage"
    assert revived.records.meta("rec_1") == {"note": "kept"}
    assert revived.records.put(Record("book", {}, "user", "x")) == "rec_2"     # numbering continues
    assert path.is_file()                                                        # backup untouched


def test_reopening_does_not_reimport_over_newer_database_state(tmp_path):
    legacy_file(tmp_path)
    store = SessionStore(store_dir=tmp_path)
    session = store.get("legacyA", "tok")
    session.messages.append({"role": "assistant", "content": "Newer reply"})
    store.save(session)
    again = SessionStore(store_dir=tmp_path).get("legacyA", "tok")
    assert again.messages[-1]["content"] == "Newer reply" and len(again.messages) == 2
    with sqlite3.connect(tmp_path / "sessions.db") as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_interrupted_import_is_rolled_back_and_redone(tmp_path, monkeypatch):
    legacy_file(tmp_path)
    original = SessionStore._write

    def fail_midway(self, session_id, header, changed, counts):
        original(self, session_id, header, changed[:2], counts)
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(SessionStore, "_write", fail_midway)
    broken = SessionStore(store_dir=tmp_path)
    assert not exists(broken, "legacyA")
    monkeypatch.setattr(SessionStore, "_write", original)
    store = SessionStore(store_dir=tmp_path)
    assert store.get("legacyA", "tok").messages[0]["content"] == "Earlier question"


def test_deleted_session_is_not_imported_again_from_a_restored_backup(tmp_path):
    path = legacy_file(tmp_path)
    store = SessionStore(store_dir=tmp_path)
    assert store.get("legacyA", "tok") is not None
    store.delete("legacyA")
    assert not path.exists() and not exists(store, "legacyA")
    legacy_file(tmp_path)                       # e.g. an old backup copied back
    assert SessionStore(store_dir=tmp_path).get("legacyA", "tok") is None


def test_unreadable_legacy_file_is_left_alone(tmp_path):
    bad = tmp_path / "broken.json"
    tmp_path.mkdir(exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    SessionStore(store_dir=tmp_path)
    assert bad.read_text(encoding="utf-8") == "{not json"


def test_failed_save_rolls_back_to_the_previous_snapshot(tmp_path, monkeypatch):
    store = SessionStore(store_dir=tmp_path)
    session, token = store.start()
    session.messages.append({"role": "user", "content": "First"})
    store.save(session)
    original = SessionStore._write

    def partial(self, session_id, header, changed, counts):
        original(self, session_id, header, changed, counts)
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(SessionStore, "_write", partial)
    session.messages = [{"role": "user", "content": "Rewritten"}, {"role": "user", "content": "Second"}]
    store.save(session)
    assert session.persistence_error
    assert parts(store, session.id)["messages"] == [{"role": "user", "content": "First"}]
    monkeypatch.setattr(SessionStore, "_write", original)
    store.save(session)                         # a retry writes everything that changed
    assert not session.persistence_error
    assert parts(store, session.id)["messages"] == session.messages


def test_saves_write_only_new_or_changed_rows(tmp_path, monkeypatch):
    store = SessionStore(store_dir=tmp_path)
    session, token = store.start()
    for index in range(5):
        session.records.put(Record("document", {"text": Field("long text " * 500, "extracted")}, "fixture", str(index)))
        session.messages.append({"role": "user", "content": f"m{index}"})
    store.save(session)
    written, encoded = [], []
    original = SessionStore._write
    monkeypatch.setattr(SessionStore, "_write",
                        lambda self, sid, header, changed, counts: (written.extend(changed), original(self, sid, header, changed, counts)))
    monkeypatch.setattr(session_module, "encode", lambda obj: encoded.append(obj) or encode(obj))
    session.messages.append({"role": "assistant", "content": "new"})
    store.save(session)
    assert [(part, key) for part, key, _ in written] == [("messages", f"{5:09d}")]
    assert encoded == []                        # unchanged records are not re-encoded
    session.messages = session.messages[:2]     # history trimmed: the tail rows go too
    store.save(session)
    revived = SessionStore(store_dir=tmp_path).get(session.id, token)
    assert [m["content"] for m in revived.messages] == ["m0", "m1"] and len(revived.records.all()) == 5


def test_expired_legacy_snapshot_is_imported_then_swept(tmp_path):
    path = legacy_file(tmp_path, expires=0)
    store = SessionStore(store_dir=tmp_path)
    assert store.get("legacyA", "tok") is None and not exists(store, "legacyA") and not path.exists()


def test_retained_legacy_snapshot_survives_expiry(tmp_path):
    legacy_file(tmp_path, expires=0, retained=True)
    assert SessionStore(store_dir=tmp_path).get("legacyA", "tok").retained
