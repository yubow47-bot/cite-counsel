"""HTTP surface of the harness: one chat page, settings, plugin assets."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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
                return fail("不允许跨站请求。", 403)
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > max_bytes + 1024 * 1024:
                return fail(f"请求太大，文件上限为 {max_upload_mb} MB。", 413)
            if not limiter.check(request.client.host if request.client else "local"):
                return fail("请求较频繁，请稍后重试。", 429)
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
            return fail("没有这个插件资源。", 404)
        path = (Path(plugin.ui) / filename).resolve()
        if path.parent != Path(plugin.ui).resolve() or not path.is_file():
            return fail("没有这个插件资源。", 404)
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
            return fail("请填写 API Key。")
        try:
            save_api_keys(key, None)
        except (ValueError, OSError) as exc:
            return fail(str(exc) if isinstance(exc, ValueError) else "无法写入本机 .env。", 500)
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
            return fail("请输入内容。")
        session, token = session_for(body)
        try:
            async with slots:
                blocks = await run_in_threadpool(_locked, session, harness.run_turn, session,
                                                 text or "请处理附件。", body.attachments)
        except LLMError as exc:
            return JSONResponse(status_code=502, content={**envelope(session, token, []), "ok": False,
                                                          "message": str(exc)})
        except Exception as exc:
            logger.warning("Turn failed: %s", type(exc).__name__, exc_info=True)
            return JSONResponse(status_code=502, content={**envelope(session, token, []), "ok": False,
                                                          "message": "这一轮没有完成，请稍后重试。"})
        return envelope(session, token, blocks)

    @app.post("/api/actions/{plugin}/{action}")
    async def action(plugin: str, action: str, body: Action):
        session, _ = session_for(body, create=False)
        if session is None:
            return fail("这次对话已过期，请重新开始。", 409)
        try:
            async with slots:
                blocks = await run_in_threadpool(_locked, session, harness.run_action, session,
                                                 plugin, action, body.payload)
        except ValueError as exc:
            return fail(str(exc), 409)
        except Exception as exc:
            logger.warning("Action %s.%s failed: %s", plugin, action, type(exc).__name__, exc_info=True)
            return fail("操作没有完成，请稍后重试。", 502)
        return {"ok": True, "blocks": blocks}

    @app.post("/api/files")
    async def upload(file: UploadFile = File(...), session_id: str = Form(default=""),
                     session_token: str = Form(default="")):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            return fail("请上传 PDF、DOCX、PPTX、XLSX 或 JPG/PNG/WebP 图片。")
        contents = await file.read(max_bytes + 1)
        if len(contents) > max_bytes:
            return fail(f"文件上限为 {max_upload_mb} MB。", 413)
        from api.main import _magic_byte_ok
        if not _magic_byte_ok(contents[:16], suffix):
            return fail("文件内容与扩展名不匹配。")
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
