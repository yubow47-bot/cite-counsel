"""Sessions: history, loaded plugins, per-plugin state, the evidence store.

A session is addressed by a random id plus a token (only its hash is kept).
Plugin state is namespaced per plugin; a plugin reaches another plugin's
state only through that plugin's ``api``, and only if it declared it in
``requires``.

Sessions persist locally: a JSON snapshot, an append-only model/tool event
log, and a directory for attachments and saved source responses. ``get``
revives a session after verifying its token; reference numbering continues.
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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.tool_contracts import Field, decode, encode
from harness.records import Store

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 40          # model-visible messages kept per session
IDLE_TTL = 4 * 3600
FILE_VERSION = 1


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
    records: Store = field(default_factory=Store)         # numbered evidence objects
    lock: threading.Lock = field(default_factory=threading.Lock)

    def user_texts(self) -> list[str]:
        """What the person typed. Notes about on-screen actions are not their words."""
        return [m["content"] for m in self.messages
                if m.get("role") == "user" and not m.get("_note") and isinstance(m.get("content"), str)]


class SessionStore:
    def __init__(self, ttl: float = IDLE_TTL, limit: int = 500, store_dir: Path | None = None):
        self.ttl, self.limit = ttl, limit
        self.store_dir = Path(store_dir) if store_dir else None
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        if self.store_dir is not None:
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
        if not session_id or not token:
            return None
        digest = hashlib.sha256(token.encode()).digest()
        with self._lock:
            session = self._sessions.get(session_id)
            if (session is not None and session.expires > time.monotonic()
                    and hmac.compare_digest(session.token_hash, digest)):
                session.expires = time.monotonic() + self.ttl
                return session
        revived = self._revive(session_id, digest)
        if revived is None:
            return None
        with self._lock:
            self._sessions[session_id] = revived
        revived.expires = time.monotonic() + self.ttl
        return revived

    # ── Persistence ───────────────────────────────────────────────────

    def append_event(self, session: Session, kind: str, payload: dict) -> None:
        """Append the exact model/tool exchange independently of session snapshots."""
        if self.store_dir is None:
            return
        self.store_dir.mkdir(parents=True, exist_ok=True)
        event = {"time": time.time(), "kind": kind, "payload": payload}
        path = self.store_dir / f"{session.id}.events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save_source(self, session: Session, ref: str, body: bytes, *, requested_url: str,
                    response: dict) -> dict:
        """Save the fetched response bytes beside the record that used them."""
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
        if self.store_dir is None:
            return
        try:
            payload = {
                "v": FILE_VERSION,
                "wall_expires": time.time() + self.ttl,
                "token_hash": session.token_hash.hex(),
                "messages": session.messages,
                "loaded": session.loaded,
                "read_skills": session.read_skills,
                "state": session.state,
                "attachments": [{"id": aid, "name": att["name"], "file": Path(att["path"]).name}
                                for aid, att in session.attachments.items()],
                "records": [{"ref": ref, "data": encode(obj), "meta": meta or None}
                            for ref, obj, meta in session.records.entries()],
            }
            self.store_dir.mkdir(parents=True, exist_ok=True)
            target = self.store_dir / f"{session.id}.json"
            tmp = target.with_suffix(f".json.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, target)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Could not persist session %s: %s", session.id, type(exc).__name__)

    def _revive(self, session_id: str, digest: bytes) -> Session | None:
        if self.store_dir is None:
            return None
        path = self.store_dir / f"{session_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("v") != FILE_VERSION:
            return None
        try:
            token_hash = bytes.fromhex(payload["token_hash"])
        except (KeyError, ValueError):
            return None
        if not hmac.compare_digest(token_hash, digest):
            return None
        session = Session(session_id, token_hash, 0.0)
        session.messages = list(payload.get("messages") or [])
        session.loaded = list(payload.get("loaded") or [])
        session.read_skills = list(payload.get("read_skills") or [])
        session.state = dict(payload.get("state") or {})
        attachments_dir = self.store_dir / session_id / "attachments"
        for att in payload.get("attachments") or []:
            path = attachments_dir / str(att.get("file") or "")
            if att.get("id") and path.is_file():
                session.attachments[att["id"]] = {"path": str(path), "name": att.get("name") or ""}
        session.records.restore([
            (entry["ref"], decode(entry["data"]), entry.get("meta") or None)
            for entry in payload.get("records") or []])
        return session

    def _sweep(self) -> None:
        """Drop session files that expired, and cap the directory."""
        try:
            files = sorted(self.store_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        now = time.time()
        kept = 0
        for path in files:
            if kept >= self.limit:
                path.unlink(missing_ok=True)
                path.with_suffix(".events.jsonl").unlink(missing_ok=True)
                self._drop_session_dir(path)
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                expired = payload.get("wall_expires", 0) < now
            except (OSError, ValueError):
                expired = True
            if expired:
                path.unlink(missing_ok=True)
                path.with_suffix(".events.jsonl").unlink(missing_ok=True)
                self._drop_session_dir(path)
            else:
                kept += 1

    def delete(self, session_id: str) -> None:
        """Forget a session for good: dropped from memory, its file and
        uploaded attachments removed from disk. No chat-history feature
        exists yet, so a "New chat" click deletes the old session outright
        rather than leaving it to expire on its own -- nothing lingers for
        a later turn to accidentally inherit."""
        with self._lock:
            self._sessions.pop(session_id, None)
        if self.store_dir is None:
            return
        path = self.store_dir / f"{session_id}.json"
        path.unlink(missing_ok=True)
        path.with_suffix(".events.jsonl").unlink(missing_ok=True)
        self._drop_session_dir(path)

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
