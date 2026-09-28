"""Per-request LLM credentials, for a caller who brings their own API key.

Every provider call in this codebase resolves its key at the last possible
moment from the ``deepseek_api._get_api_key`` choke point. Historically that
meant one key per process, read once from the environment at startup.

This module adds a second source those choke points check *first*: a
``contextvars.ContextVar`` set only for the duration of one request. A key
placed here:

- is visible only to the asyncio task that set it (and to a worker thread
  spawned from that task via ``starlette.concurrency.run_in_threadpool`` /
  ``anyio.to_thread.run_sync``, which copy the current context by design) —
  never to a concurrent request handling someone else's key;
- is never written to ``.env``, logged, or echoed back in a response;
- falls back to the process-level environment variable when absent, so
  nothing here changes behaviour for a caller that does not use it.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_MAX_KEY_LENGTH = 400


@dataclass(frozen=True)
class RequestCredentials:
    """The key one request brought with it."""

    openrouter_api_key: str | None = None

    def __post_init__(self) -> None:
        value = self.openrouter_api_key
        if value is None:
            return
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_KEY_LENGTH:
            raise ValueError(f"openrouter_api_key must be a non-empty string of at most {_MAX_KEY_LENGTH} characters")

    @property
    def empty(self) -> bool:
        return self.openrouter_api_key is None


_current: ContextVar[RequestCredentials | None] = ContextVar("chatbox_request_credentials", default=None)


@contextmanager
def use_credentials(credentials: RequestCredentials | None):
    """Make *credentials* the active per-request keys for this block.

    A ``None`` or empty *credentials* is a no-op: existing behaviour (the
    process-level environment variable) is unchanged. Always paired with a
    ``finally`` reset, so one request's key cannot leak into whatever runs
    on this task or thread afterwards.
    """
    if credentials is None or credentials.empty:
        yield
        return
    token = _current.set(credentials)
    try:
        yield
    finally:
        _current.reset(token)


def openrouter_key() -> str:
    """The active per-request OpenRouter key, else the process configuration."""
    active = _current.get()
    if active and active.openrouter_api_key:
        return active.openrouter_api_key
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY") or ""
