"""Sessions: history, loaded plugins, per-plugin state, the evidence store.

A session is addressed by a random id plus a token (only its hash is kept).
Plugin state is namespaced per plugin; a plugin reaches another plugin's
state only through that plugin's ``api``, and only if it declared it in
``requires``.

Sessions persist locally: a snapshot in ``sessions.db`` (SQLite, written
incrementally), an append-only model/tool event log, and a directory for
attachments and saved source responses. ``get`` revives a session after
verifying its token; reference numbering continues. Snapshots saved as JSON
files by earlier versions are imported once and the files kept as backups.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.tool_contracts import Field, decode, encode
from harness.records import Store
from harness.runtime import TurnBudget

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 40          # model-visible messages kept per session
IDLE_TTL = 30 * 24 * 3600  # saved local workspaces, refreshed on use
FILE_VERSION = 1
DB_NAME = "sessions.db"
LIST_PARTS = ("messages", "timeline", "tool_receipts")
VALUE_PARTS = ("loaded", "read_skills", "state", "attachments")


@dataclass
class Session:
    id: str
    token_hash: bytes
    expires: float
    messages: list[dict] = field(default_factory=list)   # OpenAI-format history
    loaded: list[str] = field(default_factory=list)       # plugins loaded by the model
    read_skills: list[str] = field(default_factory=list)  # method notes opened by the model
    state: dict[str, dict] = field(default_factory=dict)  # plugin name -> its state
    attachments: dict[str, dict] = field(default_factory=dict)
    timeline: list[dict] = field(default_factory=list)  # user-visible turns and plugin cards
    records: Store = field(default_factory=Store)         # numbered evidence objects
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancelled: threading.Event = field(default_factory=threading.Event)
    deleted: bool = False
    running: bool = False
    persistence_error: str = ""
    cancel_generation: int = 0
    active_generation: int = 0
    tool_receipts: list[dict] = field(default_factory=list)
    retained: bool = False  # an explicitly saved investigation survives chat cache expiry
    runtime: TurnBudget | None = field(default=None, repr=False)  # never persisted or reused across turns
    persisted: dict = field(default_factory=dict, repr=False)  # (part, key) -> digest last written or loaded

    def cancel(self) -> None:
        self.cancel_generation += 1
        self.cancelled.set()

    def user_texts(self) -> list[str]:
        """What the person typed. Notes about on-screen actions are not their words."""
        return [m["content"] for m in self.messages
                if m.get("role") == "user" and not m.get("_note") and isinstance(m.get("content"), str)]


class SessionStore:
    def __init__(self, ttl: float = IDLE_TTL, limit: int = 500, store_dir: Path | None = None):
        self.ttl, self.limit = ttl, limit
        self.store_dir = Path(store_dir) if store_dir else None
        self._sessions: dict[str, Session] = {}
        self._deleted: set[str] = set()
        self._lock = threading.Lock()
        self._db = None
        if self.store_dir is not None:
            self._connect()
            self._import_legacy()
            self._sweep()

    # ── In-memory ─────────────────────────────────────────────────────

    def start(self) -> tuple[Session, str]:
        token = secrets.token_urlsafe(32)
        session = Session(secrets.token_urlsafe(18), hashlib.sha256(token.encode()).digest(),
                          time.monotonic() + self.ttl)
        with self._lock:
            now = time.monotonic()
            self._sessions = {k: v for k, v in self._sessions.items() if v.expires > now}
            while len(self._sessions) >= self.limit:
                self._sessions.pop(next(iter(self._sessions)))
            self._sessions[session.id] = session
        self.save(session)
        return session, token

    def get(self, session_id: str | None, token: str | None) -> Session | None:
        if not session_id or not token or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            return None
        digest = hashlib.sha256(token.encode()).digest()
        with self._lock:
            if session_id in self._deleted:
                return None
            session = self._sessions.get(session_id)
            if (session is not None and session.expires > time.monotonic()
                    and hmac.compare_digest(session.token_hash, digest)):
                session.expires = time.monotonic() + self.ttl
                return session
            # Revival shares the same lock as deletion and cache lookup: two
            # requests must never acquire different session execution locks.
            revived = self._revive(session_id, digest)
            if revived is None:
                return None
            self._sessions[session_id] = revived
            revived.expires = time.monotonic() + self.ttl
            return revived

    # ── Persistence ───────────────────────────────────────────────────
    # Events and source bodies are files; snapshots are below.

    def append_event(self, session: Session, kind: str, payload: dict, state: dict | None = None) -> None:
        """Append the exact model/tool exchange independently of session snapshots.
        ``state`` is the harness's own turn/phase/budget snapshot, kept apart
        from the payload so the recorded exchange stays exact."""
        with self._lock:
            if session.deleted or session.id in self._deleted:
                return
            self._append_event(session, kind, payload, state)

    def _append_event(self, session: Session, kind: str, payload: dict, state: dict | None = None) -> None:
        if self.store_dir is None:
            return
        self.store_dir.mkdir(parents=True, exist_ok=True)
        event = {"time": time.time(), "kind": kind, "payload": payload, **({"state": state} if state else {})}
        path = self.store_dir / f"{session.id}.events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save_source(self, session: Session, ref: str, body: bytes, *, requested_url: str,
                    response: dict) -> dict:
        """Save the fetched response bytes beside the record that used them."""
        with self._lock:
            if session.deleted or session.id in self._deleted:
                raise ValueError("This conversation was deleted.")
            return self._save_source(session, ref, body, requested_url=requested_url, response=response)

    def _save_source(self, session: Session, ref: str, body: bytes, *, requested_url: str,
                     response: dict) -> dict:
        if self.store_dir is None:
            raise ValueError("Source snapshots require a persistent session store.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session.id) or not re.fullmatch(r"rec_\d+", ref):
            raise ValueError("Invalid source snapshot identity.")
        if not isinstance(body, bytes) or not body or len(body) > 5_000_000:
            raise ValueError("Invalid or oversized source response.")
        directory = self.store_dir / session.id / "sources"
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {"ref": ref, "requested_url": requested_url,
                    "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                    "version_as_of": None,
                    **{key: response.get(key) for key in
                       ("final_url", "status", "content_type", "etag", "last_modified")}}
        target = directory / f"{ref}.body"
        tmp = directory / f"{ref}.body.{os.getpid()}.tmp"
        tmp.write_bytes(body)
        os.replace(tmp, target)
        (directory / f"{ref}.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return metadata

    def save(self, session: Session) -> None:
        """Write the whole session out; a failure never takes the turn down."""
        with self._lock:
            if session.deleted or session.id in self._deleted:
                return
            self._save(session)

    # ── Snapshot database ─────────────────────────────────────────────
    #
    # Snapshots live in ``sessions.db``: one row per session, plus one row
    # per list item (message, timeline entry, receipt, record) or small value
    # (loaded plugins, state...). A save writes only rows whose content
    # changed since this process last saved or loaded them, in one
    # transaction. The event log stays an append-only JSONL file; attachments
    # and source bodies stay in the session's directory.

    def _connect(self) -> None:
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.store_dir / DB_NAME, check_same_thread=False, isolation_level=None,
                                 timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, token_hash TEXT NOT NULL,
                    wall_expires REAL NOT NULL, retained INTEGER NOT NULL, running INTEGER NOT NULL,
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS items(
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    part TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(session_id, part, key));
                -- Session ids whose legacy JSON snapshot must never be imported
                -- (again): already imported, or explicitly deleted.
                CREATE TABLE IF NOT EXISTS legacy_done(session_id TEXT PRIMARY KEY, at REAL NOT NULL);
            """)
            self._db = db
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Could not open the session database: %s", type(exc).__name__)
            self._db = None

    def _transaction(self, work) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            work()
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _record_marker(ref: str, meta) -> str:
        """Saved versions are immutable, so a ref plus its sidecar identifies the row."""
        return SessionStore._digest(ref + "\0" + json.dumps(meta or None, ensure_ascii=False, sort_keys=True, default=str))

    @staticmethod
    def _rows(payload: dict) -> dict[tuple[str, str], str]:
        """The list and value rows of one snapshot, as (part, key) -> JSON text."""
        rows = {}
        for part in LIST_PARTS:
            for index, item in enumerate(payload.get(part) or []):
                rows[(part, f"{index:09d}")] = json.dumps(item, ensure_ascii=False, default=str)
        for part in VALUE_PARTS:
            rows[(part, "")] = json.dumps(payload.get(part), ensure_ascii=False, default=str)
        return rows

    def _write(self, session_id: str, header: tuple, changed: list, counts: dict) -> None:
        """Upsert ``changed`` (part, key, text) rows and drop list tails past
        ``counts``. Runs inside the caller's transaction."""
        self._db.execute("INSERT INTO sessions(id, version, token_hash, wall_expires, retained, running, updated) "
                         "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version, "
                         "token_hash=excluded.token_hash, wall_expires=excluded.wall_expires, "
                         "retained=excluded.retained, running=excluded.running, updated=excluded.updated",
                         (session_id, *header))
        self._db.executemany("INSERT INTO items(session_id, part, key, data) VALUES (?,?,?,?) "
                             "ON CONFLICT(session_id, part, key) DO UPDATE SET data=excluded.data",
                             [(session_id, part, key, text) for part, key, text in changed])
        for part, count in counts.items():
            self._db.execute("DELETE FROM items WHERE session_id=? AND part=? AND key>=?",
                             (session_id, part, f"{count:09d}"))

    def _save(self, session: Session) -> None:
        if self.store_dir is None:
            return
        try:
            if self._db is None:
                raise OSError("session database unavailable")
            payload = {name: getattr(session, name) for name in ("messages", "loaded", "read_skills", "state",
                                                                  "timeline", "tool_receipts")}
            payload["attachments"] = [{"id": aid, "name": att["name"], "file": Path(att["path"]).name}
                                      for aid, att in session.attachments.items()]
            known = session.persisted
            digests, changed = {}, []
            for (part, key), text in self._rows(payload).items():
                digests[(part, key)] = self._digest(text)
                if known.get((part, key)) != digests[(part, key)]:
                    changed.append((part, key, text))
            entries = session.records.entries()
            for index, (ref, obj, meta) in enumerate(entries):
                key = ("records", f"{index:09d}")
                digests[key] = self._record_marker(ref, meta)
                if known.get(key) != digests[key]:
                    # Only a new or re-annotated version is encoded again.
                    changed.append((*key, json.dumps({"ref": ref, "data": encode(obj), "meta": meta or None},
                                                     ensure_ascii=False, default=str)))
            counts = {part: len(payload[part]) for part in LIST_PARTS} | {"records": len(entries)}
            header = (FILE_VERSION, session.token_hash.hex(), time.time() + self.ttl,
                      int(session.retained), int(session.running), time.time())
            self._transaction(lambda: self._write(session.id, header, changed, counts))
            session.persisted = digests
            session.persistence_error = ""
        except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
            session.persistence_error = "Changes are in memory but could not be saved to disk. Keep this page open and retry saving before closing the app."
            logger.warning("Could not persist session %s: %s", session.id, type(exc).__name__)

    def _revive(self, session_id: str, digest: bytes) -> Session | None:
        if self.store_dir is None or self._db is None:
            return None
        try:
            row = self._db.execute("SELECT version, token_hash, wall_expires, retained, running FROM sessions "
                                   "WHERE id=?", (session_id,)).fetchone()
            if row is None:
                return None
            version, token_hex, wall_expires, retained, running = row
            if version != FILE_VERSION or (not retained and wall_expires <= time.time()):
                return None
            token_hash = bytes.fromhex(token_hex)
            if not hmac.compare_digest(token_hash, digest):
                return None
            items = self._db.execute("SELECT part, key, data FROM items WHERE session_id=? ORDER BY part, key",
                                     (session_id,)).fetchall()
        except (sqlite3.Error, ValueError):
            return None
        lists = {part: [] for part in LIST_PARTS}
        values, entries, persisted = {}, [], {}
        try:
            for part, key, data in items:
                if part == "records":
                    entry = json.loads(data)
                    entries.append((entry["ref"], decode(entry["data"]), entry.get("meta") or None))
                    persisted[(part, key)] = self._record_marker(entry["ref"], entry.get("meta"))
                    continue
                if part in lists:
                    lists[part].append(json.loads(data))
                else:
                    values[part] = json.loads(data)
                persisted[(part, key)] = self._digest(data)
        except (ValueError, KeyError, TypeError):
            return None
        session = Session(session_id, token_hash, 0.0)
        session.messages = lists["messages"]
        session.loaded = list(values.get("loaded") or [])
        session.read_skills = list(values.get("read_skills") or [])
        session.state = dict(values.get("state") or {})
        session.tool_receipts = lists["tool_receipts"]
        session.retained = bool(retained)
        session.timeline = lists["timeline"]
        session.persisted = persisted
        if not session.timeline:
            # Older snapshots predate card persistence. Restore honest text
            # history; do not fabricate cards or reasoning for old tool calls.
            for message in session.messages:
                if message.get("_note") or message.get("role") not in {"user", "assistant"}:
                    continue
                if message.get("content"):
                    role = message["role"]
                    session.timeline.append({"role": role, "text": message["content"]} if role == "user"
                                            else {"role": role, "blocks": [{"type": "text", "text": message["content"]}]})
        attachments_dir = self.store_dir / session_id / "attachments"
        for att in values.get("attachments") or []:
            path = attachments_dir / str(att.get("file") or "")
            if att.get("id") and path.is_file():
                session.attachments[att["id"]] = {"path": str(path), "name": att.get("name") or ""}
        session.records.restore(entries)
        pending = {}
        for message in session.messages:
            if message.get("role") == "assistant":
                pending.update({call["id"]: call for call in message.get("tool_calls") or [] if call.get("id")})
            elif message.get("role") == "tool":
                pending.pop(message.get("tool_call_id"), None)
        for call_id in pending:
            session.messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps({
                "error": "The previous process stopped before this call completed. Inspect saved materials before retrying.",
                "kind": "interrupted", "retryable": None})})
        if pending or running:
            session.timeline.append({"role": "assistant", "blocks": [{"type": "notice", "level": "warning",
                "text": "The previous run was interrupted. Completed materials were restored; review them before continuing."}]})
        return session

    def _import_legacy(self) -> None:
        """Copy each pre-database JSON snapshot in once. The JSON file is kept
        as a backup; an explicitly deleted session is never imported again.
        Each import is one transaction, so an interrupted import is redone."""
        if self._db is None:
            return
        try:
            done = {row[0] for row in self._db.execute("SELECT session_id FROM legacy_done")}
            done |= {row[0] for row in self._db.execute("SELECT id FROM sessions")}
            files = sorted(self.store_dir.glob("*.json"))
        except (OSError, sqlite3.Error):
            return
        for path in files:
            session_id = path.stem
            if session_id in done or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("v") != FILE_VERSION:
                    continue
                rows = self._rows(payload)
                records = payload.get("records") or []
                for index, entry in enumerate(records):
                    rows[("records", f"{index:09d}")] = json.dumps(
                        {"ref": entry["ref"], "data": entry["data"], "meta": entry.get("meta") or None},
                        ensure_ascii=False, default=str)
                header = (FILE_VERSION, bytes.fromhex(str(payload["token_hash"])).hex(),
                          float(payload.get("wall_expires") or 0), int(payload.get("retained") is True),
                          int(bool(payload.get("running"))), path.stat().st_mtime)
                counts = {part: len(payload.get(part) or []) for part in LIST_PARTS} | {"records": len(records)}
            except (OSError, ValueError, KeyError, TypeError):
                continue  # unreadable: left on disk untouched

            def work(session_id=session_id, header=header, rows=rows, counts=counts):
                self._write(session_id, header, [(*key, text) for key, text in rows.items()], counts)
                self._db.execute("INSERT OR IGNORE INTO legacy_done(session_id, at) VALUES (?,?)",
                                 (session_id, time.time()))
            try:
                self._transaction(work)
            except sqlite3.Error:
                logger.warning("Could not import saved session %s", session_id)

    def _sweep(self) -> None:
        """Drop expired sessions, and cap how many unretained ones are kept."""
        if self._db is None:
            return
        try:
            rows = self._db.execute("SELECT id, retained, wall_expires FROM sessions ORDER BY updated DESC").fetchall()
        except sqlite3.Error:
            return
        now, kept = time.time(), 0
        for session_id, retained, wall_expires in rows:
            if retained:
                continue
            if wall_expires < now or kept >= self.limit:
                self._remove(session_id)
            else:
                kept += 1

    def _remove(self, session_id: str) -> None:
        """Delete every stored trace of a session, including a legacy JSON backup."""
        if self._db is not None:
            def work():
                self._db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                self._db.execute("INSERT OR IGNORE INTO legacy_done(session_id, at) VALUES (?,?)",
                                 (session_id, time.time()))
            try:
                self._transaction(work)
            except sqlite3.Error:
                logger.warning("Could not delete stored session %s", session_id)
        path = self.store_dir / f"{session_id}.json"
        path.unlink(missing_ok=True)
        path.with_suffix(".events.jsonl").unlink(missing_ok=True)
        self._drop_session_dir(path)

    def delete(self, session_id: str) -> None:
        """Explicit deletion cancels the run and prevents stale workers saving it again."""
        with self._lock:
            self._deleted.add(session_id)
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.deleted = True
                session.cancelled.set()
            if self.store_dir is None:
                return
            self._remove(session_id)

    def _drop_session_dir(self, session_file: Path) -> None:
        try:
            target = (self.store_dir / session_file.stem).resolve()
            if target.parent != self.store_dir.resolve():
                raise ValueError("Session directory escaped the store.")
            shutil.rmtree(target, ignore_errors=True)
        except OSError:
            pass

    def attachment_dir(self, session: Session) -> Path:
        """Where this session's uploads live (stable across restarts)."""
        if session.deleted:
            raise ValueError("This conversation was deleted.")
        directory = self.store_dir / session.id / "attachments"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


class Context:
    """What a plugin sees during one tool call or action."""

    def __init__(self, session: Session, plugin: str, harness, settings: dict | None = None):
        self.session, self.plugin, self._harness = session, plugin, harness
        self.settings = settings or {}
        # Filled in by the harness before the handler runs: the user's words
        # verified for this call (UserText parameters) and the call's own
        # parameter values. Direct calls (tests) start from nothing.
        self.user_values: frozenset[str] = frozenset()
        self.param_values: frozenset[str] = frozenset()
        self.call_id: str | None = None

    @property
    def completion_review(self) -> dict | None:
        runtime = self.session.runtime
        if runtime and self.call_id and runtime.reviewed_call_id == self.call_id:
            return dict(runtime.review_audit)
        return None

    def request_review(self, messages: list[dict], *, tools=(), tool_choice=None, mode="candidate"):
        """Use the harness's one bounded review allowance, never a private loop."""
        return self._harness.request_review(self.session, messages, list(tools), tool_choice=tool_choice, mode=mode)

    @property
    def records(self) -> Store:
        """The session's numbered evidence store."""
        return self.session.records

    @property
    def claimable_user_values(self) -> frozenset[str]:
        """Only values checked against the person's own messages may claim user origin."""
        return self.user_values

    def save(self, obj, meta: dict | None = None, *, supersedes: str | None = None) -> str:
        """Store an object under its next number, after the category and
        provenance checks. This is the only way a plugin puts evidence into
        the session; ``harness`` (the built-in record tools) is exempt from
        the category check because user-origin fields are its to write.

        ``supersedes`` marks this as a newer version of an earlier ref, so a
        caller that still cites the old one lands on this version instead."""
        if self.session.deleted or self.session.cancelled.is_set():
            raise ValueError("This run was stopped; no new material was saved.")
        if self.plugin != "harness":
            category = getattr(self._harness.plugins[self.plugin], "category", "function")
            obj = self.session.records.check_output(category, [obj], self.claimable_user_values)[0]
        return self.session.records.put(obj, meta, supersedes=supersedes)

    @property
    def state(self) -> dict:
        return self.session.state.setdefault(self.plugin, {})

    def user_texts(self) -> list[str]:
        return self.session.user_texts()

    def user_said(self, value: str) -> str:
        """``value`` as the user actually wrote it in this session, or "".

        The generic grounding check: a tool that must not accept model-made
        values accepts only slices of the user's own messages.
        """
        import re
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            return ""
        probe = re.sub(r"\s+", " ", value).strip().casefold()
        for text in reversed(self.user_texts()):
            flat = re.sub(r"\s+", " ", text)
            from core.evidence_text import find_text
            at = find_text(flat, probe)
            if at >= 0:
                return flat[at:at + len(probe)]
        return ""

    def provenance(self, value: str) -> tuple[Field, str]:
        """The honest origin of a value the model or the user supplied: the
        stored record it was copied from (with that record's ref, including a
        value read out of a record's longer text), or the user's own words.

        Nothing is refused. A value no one in this session can be traced to
        is kept as ``model`` -- written and shown, labelled unverified, so the
        user still gets the output and can see exactly which part to check.
        """
        from core.tool_contracts import Field
        field, ref = self.records.resolve(value)
        if field.origin != "model":
            return field, ref
        said = self.user_said(value)
        if said:
            return Field(said, "user"), ""
        return Field(value, "model"), ""

    def attachment(self, attachment_id: str) -> dict | None:
        found = self.session.attachments.get(attachment_id)
        return found if found and Path(found["path"]).is_file() else None

    def use(self, name: str):
        """(api, context) of a plugin this one declared in ``requires``."""
        return self._harness.dependency(self.plugin, name, self.session)

    @property
    def model(self) -> str:
        """The model configured for ordinary tool-deciding turns."""
        return self._harness.config["model"]

    def is_enabled(self, name: str) -> bool:
        """Whether another plugin is switched on; its tools stay out of reach."""
        return name in self._harness.config["enabled"]

    def log_event(self, kind: str, data: dict) -> None:
        """Append to this session's audit log (not the model's history)."""
        self._harness.log_event(self.session, kind, data)

    def save_source(self, ref: str, body: bytes, *, requested_url: str, response: dict) -> dict:
        """Keep the raw bytes a source record ``ref`` was built from."""
        return self._harness.sessions.save_source(self.session, ref, body,
                                                  requested_url=requested_url, response=response)
