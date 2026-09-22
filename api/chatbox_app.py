"""Single-port local HTTP UI and controlled citation workflow.

Start with run_chatbox.py so isolated OpenRouter settings load first.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core import chatbox_service as service
from llm_api.request_credentials import RequestCredentials, use_credentials

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=2000)
    # What the user currently has on screen, so a message like "the second one"
    # or "the year is wrong" has something to refer to. Unknown or expired ids
    # are ignored rather than rejected: the turn is then a fresh search.
    item_id: str | None = Field(default=None, max_length=64)
    item_token: str | None = Field(default=None, max_length=128)
    candidate_set_id: str | None = Field(default=None, max_length=64)
    candidate_token: str | None = Field(default=None, max_length=128)
    # Bring-your-own key: used only for the provider calls this turn makes,
    # never persisted, never echoed back. See llm_api.request_credentials.
    openrouter_api_key: SecretStr | None = Field(default=None)
    typesafe_api_key: SecretStr | None = Field(default=None)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_set_id: str = Field(min_length=1, max_length=64)
    access_token: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=64)
    openrouter_api_key: SecretStr | None = Field(default=None)
    typesafe_api_key: SecretStr | None = Field(default=None)


class FieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=64)
    access_token: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, le=500)
    fields: dict[str, str]


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="rating", max_length=20)
    verdict: str | None = Field(default=None, max_length=10)
    input: str | None = Field(default=None, max_length=2000)
    output: str | None = Field(default=None, max_length=4000)
    route: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=4000)


class ApiKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    openrouter_api_key: SecretStr | None = Field(default=None)
    typesafe_api_key: SecretStr | None = Field(default=None)


def success(blocks):
    return {"ok": True, "blocks": blocks}


def failure(message, status=400):
    return JSONResponse(status_code=status, content={"ok": False, "message": message,
                                                    "blocks": [service.notice(message, "error")]})


def _credentials(openrouter_api_key: SecretStr | None, typesafe_api_key: SecretStr | None) -> RequestCredentials | None:
    """Build per-request credentials from optional secret fields, or None to defer to the server's own configuration."""
    openrouter = (openrouter_api_key.get_secret_value().strip() if openrouter_api_key else "")
    typesafe = (typesafe_api_key.get_secret_value().strip() if typesafe_api_key else "")
    if not openrouter and not typesafe:
        return None
    try:
        return RequestCredentials(openrouter_api_key=openrouter or None, typesafe_api_key=typesafe or None)
    except ValueError:
        return None


def create_app(settings: dict | None = None) -> FastAPI:
    if settings is None:
        from core.chatbox_settings import configure
        settings = configure()
    app = FastAPI(title="Cite Counsel Chatbox", docs_url=None, redoc_url=None, openapi_url=None)
    max_bytes = settings["max_upload_mb"] * 1024 * 1024
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    from api.rate_limiter import RateLimiter
    limiter = RateLimiter()
    # Local beta has no persistent sessions: only two external operations run
    # concurrently, candidate sets expire, and refresh clears the conversation.
    slots = asyncio.Semaphore(2)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return failure("不允许跨站请求。", 403)
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > max_bytes + 1024 * 1024:
                return failure(f"请求太大，文件上限为 {settings['max_upload_mb']} MB。", 413)
            if not limiter.check(request.client.host if request.client else "local"):
                return failure("请求较频繁，请稍后重试。", 429)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get("/")
    @app.get("/chatbox")
    def page():
        return FileResponse(ROOT / "frontend/chatbox/index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "frontend/chatbox"), name="assets")

    @app.get("/api/chatbox/config")
    def config():
        llm_configured = bool(settings["openrouter_api_key"])
        jev_configured = bool(settings["typesafe_api_key"])
        configured = llm_configured and jev_configured
        return {"provider": "openrouter", "jev_provider": "typesafe",
                "configured": configured, "llm_configured": llm_configured,
                "jev_configured": jev_configured,
                "mode": "live" if configured else "partial" if llm_configured or jev_configured else "unconfigured",
                "llm_model": settings["llm_model"], "vision_model": settings["vision_model"],
                "jev_model": settings["jev_model"], "jev_shadow": settings["jev_shadow"],
                "max_upload_mb": settings["max_upload_mb"]}

    @app.get("/api/health")
    def health():
        return {"ok": True, "service": "chatbox"}

    @app.post("/api/chatbox/settings/keys")
    def save_keys(body: ApiKeys):
        openrouter_key = body.openrouter_api_key.get_secret_value() if body.openrouter_api_key else None
        typesafe_key = body.typesafe_api_key.get_secret_value() if body.typesafe_api_key else None
        if not (openrouter_key and openrouter_key.strip()) and not (typesafe_key and typesafe_key.strip()):
            return failure("请至少填写一个 API Key。")
        try:
            from core.chatbox_settings import save_api_keys
            status = save_api_keys(openrouter_key, typesafe_key)
            settings["openrouter_api_key"] = os.getenv("OPENROUTER_API_KEY", "").strip()
            settings["typesafe_api_key"] = os.getenv("TYPESAFE_API_KEY", "").strip()
            return {"ok": True, **status, "message": "API Key 已保存到本机 .env，当前服务已应用。"}
        except ValueError as exc:
            return failure(str(exc))
        except OSError:
            logger.exception("Could not persist Chatbox API keys")
            return failure("无法写入本机 .env，请检查项目目录权限。", 500)

    @app.post("/api/chatbox/turns")
    async def turns(body: Turn):
        text = body.input.strip()
        if not text:
            return failure("请输入内容。")
        if text.lower() in {"你好", "hello", "hi", "help", "帮助"}:
            return success([service.notice("把案名、引用号、URL、DOI、ISBN 或文件发到这里即可。")])
        if len([line for line in text.splitlines() if line.strip()]) > 1:
            return success([service.notice("目前每次处理一条引用。请拆开发送，避免遗漏。", "warning")])
        kind, value = service.route_input(text)
        if kind == "invalid_url":
            return failure("请只粘贴一个完整的 HTTP(S) URL。")
        # A key on this request is visible only for the provider calls this
        # turn makes below; use_credentials resets it before the handler returns.
        credentials = _credentials(body.openrouter_api_key, body.typesafe_api_key)
        # A pasted DOI/ISBN/URL is always a new source. Anything else, with a
        # result or a candidate list on screen, may be about that instead, so the
        # planner gets first refusal.
        if kind == "query":
            context = service.turn_context(body.item_id, body.item_token,
                                           body.candidate_set_id, body.candidate_token)
            if context.active:
                with use_credentials(credentials):
                    async with slots:
                        planned = await run_in_threadpool(service.planned_blocks, text, context)
                if planned is not None:
                    return success(planned)
        if kind in {"query", "url"} and not settings["openrouter_api_key"] and not credentials:
            return success([service.notice("请先在左侧“API Key 设置”填入 OpenRouter API Key，或在本次消息中附带你自己的 Key。", "warning")])
        try:
            with use_credentials(credentials):
                async with slots:
                    if kind in {"doi", "isbn"}:
                        blocks = await run_in_threadpool(service.identifier_blocks, kind, value)
                    elif kind == "url":
                        from llm_api.deepseek_api import extract_from_url
                        fields = await run_in_threadpool(extract_from_url, value)
                        blocks = await run_in_threadpool(service.extracted_blocks, fields, settings["jev_shadow"], webpage=True)
                    else:
                        blocks = await run_in_threadpool(service.query_blocks, value)
            return success(blocks)
        except Exception as exc:
            logger.warning("Chatbox turn failed: %s", type(exc).__name__)
            return failure("本次检索未完成，请检查网络、OpenRouter 配置或稍后重试。", 502)

    @app.post("/api/chatbox/select")
    async def select(body: Selection):
        try:
            record = service.candidate_store.get(body.candidate_set_id, body.access_token, body.candidate_id)
            credentials = _credentials(body.openrouter_api_key, body.typesafe_api_key)
            with use_credentials(credentials):
                async with slots:
                    return success(await run_in_threadpool(service.record_blocks, record))
        except ValueError as exc:
            return failure(str(exc), 409)
        except Exception as exc:
            logger.warning("Candidate selection failed: %s", type(exc).__name__)
            return failure("候选处理未完成，请重新查询。", 502)

    @app.post("/api/chatbox/items/fields")
    def item_fields(body: FieldUpdate):
        """Apply user-supplied field values to a stored item and re-render it.

        Deterministic and free: the citation is assembled from
        mcgill_rules.json, so no provider is called and no budget is spent.
        """
        if len(body.fields) > 20 or any(len(name) > 64 for name in body.fields):
            return failure("提交的字段过多。")
        try:
            item = service.item_store.update(body.item_id, body.access_token, body.revision, body.fields)
        except ValueError as exc:
            return failure(str(exc), 409)
        return success(service.item_blocks(body.item_id, body.access_token, item))

    @app.post("/api/chatbox/feedback")
    def feedback(body: Feedback):
        """Original 👍/👎 and message feedback, written to data/feedback.jsonl only.

        The legacy handler also queues HF Dataset / Discord delivery as
        background tasks; those tasks are deliberately never run here.
        """
        from fastapi import BackgroundTasks
        from api.main import FeedbackInput, feedback as legacy_feedback
        envelope = legacy_feedback(FeedbackInput(**body.model_dump()), BackgroundTasks())
        if envelope.get("status") == "done":
            return {"ok": True, "message": "已记录，谢谢反馈。"}
        return failure((envelope.get("error") or {}).get("reason") or "反馈未能保存。")

    warm_state = {"at": 0.0}

    @app.get("/api/chatbox/warmup")
    async def warmup():
        """Original cold-start mitigation, limited to services the Chatbox uses."""
        import time
        if time.monotonic() - warm_state["at"] < 300:
            return {"ok": True, "cached": True}
        warm_state["at"] = time.monotonic()
        return {"ok": True, **await run_in_threadpool(service.warm_up)}

    @app.post("/api/chatbox/files")
    async def files(file: UploadFile = File(...), input: str = Form(default="", max_length=2000),
                    openrouter_api_key: str = Form(default="", max_length=400),
                    typesafe_api_key: str = Form(default="", max_length=400)):
        path = None
        try:
            from api.main import _magic_byte_ok, _UPLOAD_SUFFIXES
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in _UPLOAD_SUFFIXES:
                return failure("请上传 PDF、DOCX、PPTX、XLSX 或 JPG/PNG/WebP 图片。")
            contents = await file.read(max_bytes + 1)
            if len(contents) > max_bytes:
                return failure(f"文件上限为 {settings['max_upload_mb']} MB。", 413)
            if not _magic_byte_ok(contents[:16], suffix):
                return failure("文件内容与扩展名不匹配，请检查文件。")
            try:
                credentials = (RequestCredentials(openrouter_api_key=openrouter_api_key.strip() or None,
                                                  typesafe_api_key=typesafe_api_key.strip() or None)
                              if (openrouter_api_key.strip() or typesafe_api_key.strip()) else None)
            except ValueError:
                return failure("API Key 格式不正确。")
            if not settings["openrouter_api_key"] and not credentials:
                return success([service.notice("文件识别需要 OpenRouter API Key。请填写本机配置，或在上传时附带你自己的 Key。", "warning")])
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                path = tmp.name
                tmp.write(contents)
            from local_tools.file_extractor import extract_from_file
            with use_credentials(credentials):
                async with slots:
                    fields = await run_in_threadpool(extract_from_file, path)
                    blocks = await run_in_threadpool(service.extracted_blocks, fields, settings["jev_shadow"])
            if input.strip():
                blocks.insert(0, service.notice("已保留附件说明：" + input.strip() + "\n本次按附件内容处理；若说明包含另一条引用，请另行发送。"))
            return success(blocks)
        except Exception as exc:
            logger.warning("Chatbox file failed: %s", type(exc).__name__)
            return failure("文件提取未完成，请检查配置或尝试更清晰的文件。", 502)
        finally:
            await file.close()
            if path:
                Path(path).unlink(missing_ok=True)

    return app
