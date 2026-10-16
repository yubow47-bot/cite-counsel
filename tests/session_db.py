"""Test helpers that read or age the session snapshot database directly."""
import json
import sqlite3


def _connect(store):
    return sqlite3.connect(store.store_dir / "sessions.db")


def exists(store, session_id):
    with _connect(store) as db:
        return db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None


def set_wall_expires(store, session_id, value):
    """Age a saved session without waiting for real time to pass."""
    with _connect(store) as db:
        db.execute("UPDATE sessions SET wall_expires=? WHERE id=?", (value, session_id))


def parts(store, session_id):
    """Stored snapshot parts: lists in order, values as saved."""
    result = {}
    with _connect(store) as db:
        for part, key, data in db.execute("SELECT part, key, data FROM items WHERE session_id=? ORDER BY part, key",
                                          (session_id,)):
            if key:
                result.setdefault(part, []).append(json.loads(data))
            else:
                result[part] = json.loads(data)
    return result
