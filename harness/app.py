"""HTTP surface of the harness: one chat page, settings, plugin assets."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import re
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from harness.core import Harness
from harness.llm import LLMError

logger = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent / "web"
UPLOAD_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".jpg", ".jpeg", ".png", ".webp"}


class SessionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, max_length=64)
    session_token: str | None = Field(default=None, max_length=128)


class Turn(SessionRef):
    input: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list, max_length=5)


class Action(SessionRef):
    payload: dict = Field(default_factory=dict)


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")


class Keys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    openrouter_api_key: SecretStr


class PluginUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    settings: dict | None = None


def fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "message": message})


def create_app(harness: Harness | None = None, *, max_upload_mb: int = 50) -> FastAPI:
    if harness is None:
        from core.chatbox_settings import configure
        settings = configure()
        harness = Harness(model=settings["llm_model"])
        max_upload_mb = settings["max_upload_mb"]
    app = FastAPI(title="Cite Counsel", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.harness = harness
    max_bytes = max_upload_mb * 1024 * 1024
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    from api.rate_limiter import RateLimiter
    limiter = RateLimiter()
    slots = asyncio.Semaphore(2)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return fail("Cross-site requests are not allowed.", 403)
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > max_bytes + 1024 * 1024:
                return fail(f"Request too large; the file limit is {max_upload_mb} MB.", 413)
            if not limiter.check(request.client.host if request.client else "local"):
                return fail("Too many requests -- please try again shortly.", 429)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        return response

    def session_for(ref: SessionRef, *, create: bool = True):
        session = harness.sessions.get(ref.session_id, ref.session_token)
        if session is not None:
            return session, None
        if not create:
            return None, None
        return harness.sessions.start()

    def envelope(session, token, blocks):
        body = {"ok": True, "blocks": blocks}
        if token:
            body["session"] = {"id": session.id, "token": token}
        return body

    @app.get("/")
    def page():
        return FileResponse(WEB / "index.html")

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")

    @app.get("/plugins/{name}/{filename}")
    def plugin_asset(name: str, filename: str):
        plugin = harness.plugins.get(name)
        if (plugin is None or plugin.ui is None or name not in harness.config["enabled"]
                or not re.fullmatch(r"[a-z0-9_-]+\.(js|css)", filename)):
            return fail("No such plugin asset.", 404)
        path = (Path(plugin.ui) / filename).resolve()
        if path.parent != Path(plugin.ui).resolve() or not path.is_file():
            return fail("No such plugin asset.", 404)
        return FileResponse(path)

    @app.get("/api/config")
    def config():
        return {**harness.describe(), "max_upload_mb": max_upload_mb}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/settings/model")
    def set_model(body: ModelChoice):
        harness.set_model(body.model)
        return {"ok": True, **harness.describe()}

    @app.post("/api/settings/keys")
    def set_keys(body: Keys):
        from core.chatbox_settings import save_api_keys
        key = body.openrouter_api_key.get_secret_value().strip()
        if not key:
            return fail("Please enter an API key.")
        try:
            save_api_keys(key)
        except (ValueError, OSError) as exc:
            return fail(str(exc) if isinstance(exc, ValueError) else "Could not write to the local .env file.", 500)
        return {"ok": True, **harness.describe()}

    @app.post("/api/settings/plugins/{name}")
    def update_plugin(name: str, body: PluginUpdate):
        try:
            if body.settings:
                harness.set_plugin_settings(name, body.settings)
            if body.enabled is not None:
                harness.set_enabled(name, body.enabled)
        except ValueError as exc:
            return fail(str(exc), 409)
        return {"ok": True, **harness.describe()}

    @app.post("/api/turns")
    async def turn(body: Turn):
        text = body.input.strip()
        if not text and not body.attachments:
            return fail("Please enter a message.")
        session, token = session_for(body)
        try:
            async with slots:
                blocks = await run_in_threadpool(_locked, session, harness.run_turn, session,
                                                 text or "Please process the attachment.", body.attachments)
        except LLMError as exc:
            return JSONResponse(status_code=502, content={**envelope(session, token, []), "ok": False,
                                                          "message": str(exc)})
        except Exception as exc:
            logger.warning("Turn failed: %s", type(exc).__name__, exc_info=True)
            return JSONResponse(status_code=502, content={**envelope(session, token, []), "ok": False,
                                                          "message": "This turn did not complete -- please try again shortly."})
        return envelope(session, token, blocks)

    @app.post("/api/turns/stream")
    def turn_stream(body: Turn):
        text = body.input.strip()
        if not text and not body.attachments:
            return fail("Please enter a message.")
        session, token = session_for(body)

        def frames():
            if token:
                yield _sse("session", {"session": {"id": session.id, "token": token}})
            try:
                for kind, payload in _stream_locked(session, harness.run_turn_stream, session,
                                                    text or "Please process the attachment.", body.attachments):
                    if kind == "thinking_delta":
                        yield _sse("thinking_delta", {"text": payload})
                    elif kind == "block":
                        yield _sse("block", {"block": payload})
            except LLMError as exc:
                yield _sse("error", {"message": str(exc)})
            except Exception as exc:
                logger.warning("Streamed turn failed: %s", type(exc).__name__, exc_info=True)
                yield _sse("error", {"message": "This turn did not complete -- please try again shortly."})
            yield _sse("done", {})

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/sessions/close")
    def close_session(body: SessionRef):
        # No chat-history feature exists yet, so "New chat" deletes the old
        # session outright instead of leaving it to expire on its own --
        # the token proves the caller actually owns it (session_for with
        # create=False returns None for a wrong or already-gone one).
        session, _ = session_for(body, create=False)
        if session is not None:
            harness.sessions.delete(session.id)
        return {"ok": True}

    @app.post("/api/actions/{plugin}/{action}")
    async def action(plugin: str, action: str, body: Action):
        session, _ = session_for(body, create=False)
        if session is None:
            return fail("This conversation has expired -- please start a new one.", 409)
        try:
            async with slots:
                blocks = await run_in_threadpool(_locked, session, harness.run_action, session,
                                                 plugin, action, body.payload)
        except ValueError as exc:
            return fail(str(exc), 409)
        except Exception as exc:
            logger.warning("Action %s.%s failed: %s", plugin, action, type(exc).__name__, exc_info=True)
            return fail("The action did not complete -- please try again shortly.", 502)
        return {"ok": True, "blocks": blocks}

    @app.post("/api/files")
    async def upload(file: UploadFile = File(...), session_id: str = Form(default=""),
                     session_token: str = Form(default="")):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            return fail("Please upload a PDF, DOCX, PPTX, XLSX, or a JPG/PNG/WebP image.")
        contents = await file.read(max_bytes + 1)
        if len(contents) > max_bytes:
            return fail(f"The file limit is {max_upload_mb} MB.", 413)
        from api.main import _magic_byte_ok
        if not _magic_byte_ok(contents[:16], suffix):
            return fail("The file content does not match its extension.")
        session, token = session_for(SessionRef(session_id=session_id or None, session_token=session_token or None))
        attachment_id = "att_" + secrets.token_hex(4)
        # The file lives inside the session's own directory, so it survives
        # a restart together with the record that points at it.
        path = harness.sessions.attachment_dir(session) / (attachment_id + suffix)
        path.write_bytes(contents)
        session.attachments[attachment_id] = {"path": str(path), "name": Path(file.filename or "file").name[:120]}
        harness.sessions.save(session)
        return {**envelope(session, token, []), "attachment": {"id": attachment_id,
                                                               "name": session.attachments[attachment_id]["name"]}}

    return app


def _locked(session, fn, *args):
    """One turn or action at a time per session; other sessions run in parallel."""
    with session.lock:
        return fn(*args)


# Streamed turns run on their own worker threads, outside the asyncio
# ``slots`` the JSON endpoints use; this caps them the same way (2 at once).
_STREAM_SLOTS = threading.BoundedSemaphore(2)


def _sse(event: str, data: dict) -> str:
    return f"data: {json.dumps({'type': event, **data}, ensure_ascii=False, default=str)}\n\n"


def _stream_locked(session, gen_fn, *args):
    """Bridge a synchronous generator that makes blocking network calls
    (``run_turn_stream``, streaming from the model) into the request: held
    for the session's lock the whole time like ``_locked``, but each item
    handed to the caller as soon as it is produced instead of all collected
    first. Starlette runs a sync generator's ``next()`` in a threadpool, so
    the blocking ``queue.get()`` below does not stall the event loop."""
    events: queue.Queue = queue.Queue()
    DONE = object()

    def worker():
        with _STREAM_SLOTS, session.lock:
            try:
                for item in gen_fn(*args):
                    events.put(("item", item))
            except BaseException as exc:  # noqa: BLE001 -- re-raised on the caller's side below
                events.put(("error", exc))
            finally:
                events.put(("end", DONE))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        kind, payload = events.get()
        if kind == "end":
            return
        if kind == "error":
            raise payload
        yield payload
