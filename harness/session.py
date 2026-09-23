"""Short-lived anonymous sessions: history, loaded plugins, per-plugin state.

A session is addressed by a random id plus a token (only its hash is kept).
Plugin state is namespaced per plugin; a plugin reaches another plugin's
state only through that plugin's ``api``, and only if it declared it in
``requires``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 40          # model-visible messages kept per session
IDLE_TTL = 4 * 3600


@dataclass
class Session:
    id: str
    token_hash: bytes
    expires: float
    messages: list[dict] = field(default_factory=list)   # OpenAI-format history
    loaded: list[str] = field(default_factory=list)       # plugins loaded by the model
    state: dict[str, dict] = field(default_factory=dict)  # plugin name -> its state
    attachments: dict[str, dict] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def user_texts(self) -> list[str]:
        """What the person typed. Notes about on-screen actions are not their words."""
        return [m["content"] for m in self.messages
                if m.get("role") == "user" and not m.get("_note") and isinstance(m.get("content"), str)]


class SessionStore:
    def __init__(self, ttl: float = IDLE_TTL, limit: int = 500):
        self.ttl, self.limit = ttl, limit
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

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
        return session, token

    def get(self, session_id: str | None, token: str | None) -> Session | None:
        if not session_id or not token:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if (session is None or session.expires <= time.monotonic()
                    or not hmac.compare_digest(session.token_hash, hashlib.sha256(token.encode()).digest())):
                return None
            session.expires = time.monotonic() + self.ttl
            return session


class Context:
    """What a plugin sees during one tool call or action."""

    def __init__(self, session: Session, plugin: str, harness, settings: dict | None = None):
        self.session, self.plugin, self._harness = session, plugin, harness
        self.settings = settings or {}

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
            at = flat.casefold().find(probe)
            if at >= 0:
                return flat[at:at + len(probe)]
        return ""

    def attachment(self, attachment_id: str) -> dict | None:
        found = self.session.attachments.get(attachment_id)
        return found if found and Path(found["path"]).is_file() else None

    def use(self, name: str):
        """(api, context) of a plugin this one declared in ``requires``."""
        return self._harness.dependency(self.plugin, name, self.session)
