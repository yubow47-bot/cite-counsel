"""McGill Citation Tool — FastAPI HTTP wrapper.

Adds REST API at /api/* for Next.js frontend and future Chrome Extension.

Run:  uvicorn api.main:app --reload --port 8000
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Ensure project root is on sys.path (so `from local_tools …` works) ──
_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

# ── API-local modules ──
from api.rate_limiter import RateLimiter, extract_client_ip
from api.scaffold import (
    assemble,
    build_prefill,
    get_type_options,
    get_field_configs,
    SCAFFOLD_ELIGIBLE_ROUTES,
    SUGGESTED_TYPE_MAP,
)

# ── Spend cap tracker ──
from core.spend_tracker import spend_tracker

# ── Existing pipeline imports (no changes to these modules) ──
from local_tools.citation_search import classify_and_normalize, search_citation
from local_tools.file_extractor import extract_from_file, classify_document_type
from local_tools.openlibrary_api import extract_isbn
from llm_api.deepseek_api import extract_from_url, chat_deepseek
from core.mcgill_engine import format_citation, get_last_debug, detect_type, get_rules

# ═══════════════════════════════════════════════════════════════════
#  App setup
# ═══════════════════════════════════════════════════════════════════

DEBUG = os.getenv("DEBUG_RESPONSES", "false").lower() in ("1", "true", "yes")
SCAFFOLD_ENABLED = os.getenv("SCAFFOLD_ENABLED", "false").lower() in ("1", "true", "yes")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(title="McGill Citation Tool API", version="1.0.0")
rate_limiter = RateLimiter()

# ── CORS ──
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ═══════════════════════════════════════════════════════════════════
#  Pydantic models
# ═══════════════════════════════════════════════════════════════════

class CitationInput(BaseModel):
    input: str


class CitationSelectInput(BaseModel):
    candidates: list
    selected_index: int


class UrlInput(BaseModel):
    url: Optional[str] = None
    doi: Optional[str] = None
    isbn: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatInput(BaseModel):
    messages: list[ChatMessage]


class FeedbackInput(BaseModel):
    kind: str = "rating"  # "rating" | "message"
    input: Optional[str] = None
    output: Optional[str] = None
    route: Optional[str] = None
    verdict: Optional[str] = None  # "up" or "down" for ratings
    note: Optional[str] = None


class AssemblyInput(BaseModel):
    type: str
    fields: dict


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _envelope(
    ok: bool,
    route: str,
    status: str,
    data: dict,
    debug: dict | None = None,
    error: dict | None = None,
) -> dict:
    """Build the unified response envelope.

    debug is *always* stripped when DEBUG_RESPONSES is false (production).
    """
    return {
        "ok": ok,
        "route": route,
        "status": status,
        "data": data,
        "debug": debug if DEBUG else None,
        "error": error,
    }


def _build_debug(route_label: str) -> dict | None:
    """Build debug payload from the last format_citation call + route info."""
    if not DEBUG:
        return None
    last = get_last_debug()
    return {
        "route": route_label,
        "a2aj_summary": last.get("source", ""),
        "deepseek_prompt": (last.get("prompt") or "")[:1000],
        "deepseek_response": (last.get("raw_response") or "")[:2000],
        "rule_used": last.get("source"),
    }


def _candidate_display(item: dict) -> str:
    """Build a human-readable label for a single candidate."""
    badge = "✅" if item.get("verified") else "⚠️"
    name = (
        item.get("style_of_cause")
        or item.get("statute_title")
        or item.get("name")
        or "unknown"
    )
    cit = item.get("neutral_citation") or item.get("reporter", "")
    parts = [f"{badge} {name}"]
    if cit:
        parts.append(cit)
    return " — ".join(parts)


def _check_spend_cap():
    """Return a 503 JSONResponse if the daily spend cap is reached, else None."""
    if spend_tracker.is_over_cap():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "route": "",
                "status": "error",
                "data": {},
                "debug": None,
                "error": {"reason": "Daily service capacity reached. Please try again tomorrow."},
            },
        )
    return None


def _collect_debug_info(route_label: str):
    """Return debug dict respecting DEBUG_RESPONSES flag."""
    if not DEBUG:
        return None
    last = get_last_debug()
    return {
        "route": route_label,
        "a2aj_summary": last.get("source", ""),
        "deepseek_prompt": (last.get("prompt") or "")[:1000],
        "deepseek_response": (last.get("raw_response") or "")[:2000],
        "rule_used": last.get("source"),
    }


# ── Scaffold gating ───────────────────────────────────────────────────

_SCAFFOLD_DISABLED_MSG = (
    "We couldn't verify this against our legal databases, so no citation was "
    "generated. This tool currently covers sources it can verify — cases, "
    "legislation, journals, and books."
)

_SCAFFOLD_DISABLED_MSG_URL = (
    "We couldn't read this URL (some sites block automated access). "
    "Try uploading a full-page screenshot instead."
)


def _scaffold_response(
    route: str,
    message: str,
    *,
    prefill: dict | None = None,
    suggested_type: str | None = None,
    disabled_message: str | None = None,
) -> dict:
    """Return needs_input when scaffold is enabled, unsupported when disabled.

    When disabled, returns an unsupported envelope with ``disabled_message``
    (falls back to ``message`` when no specific disabled_message given).
    """
    if SCAFFOLD_ENABLED:
        data: dict = {"message": message}
        if prefill:
            data["prefill"] = prefill
        if suggested_type:
            data["type"] = suggested_type
        return _envelope(True, route, "needs_input", data)

    return _envelope(
        True, route, "unsupported", {},
        error={"reason": disabled_message or message},
    )


# ═══════════════════════════════════════════════════════════════════
#  1. POST /api/citation  —  Citation query (hero flow)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/citation")
async def citation_query(body: CitationInput, request: Request):
    """Search legal citations.  Routes to direct format or candidate selection."""
    if not body.input or not body.input.strip():
        return _envelope(False, "", "error", {}, error={"reason": "Input cannot be empty"})

    cap_block = _check_spend_cap()
    if cap_block:
        return cap_block

    query = body.input.strip()

    # ── Step 1: classify ──
    try:
        classified = classify_and_normalize(query)
    except Exception as e:
        return _envelope(False, "", "error", {}, error={"reason": f"Classification failed: {e}"})

    route = classified["type"]

    # ── Step 2: search ──
    try:
        results = search_citation(query, classification=classified)
    except Exception as e:
        return _envelope(
            False, route, "error", {}, error={"reason": f"Search failed: {e}"}
        )

    if not results:
        # Grounding failed — offer scaffold if a template exists for this route
        if route in SCAFFOLD_ELIGIBLE_ROUTES:
            prefill = build_prefill(route, query)
            suggested = SUGGESTED_TYPE_MAP.get(route, route)
            return _scaffold_response(
                route,
                "Could not verify against our databases. Fill in the fields below to generate a McGill 10th citation.",
                prefill=prefill,
                suggested_type=suggested,
                disabled_message=_SCAFFOLD_DISABLED_MSG,
            )
        return _envelope(
            True, route, "unsupported",
            {}, error={"reason": "No matching results found. Try different keywords."},
        )

    # ── concept: multi-result, format each ──
    if route == "concept":
        return _handle_concept(results)

    # ── citation_number / legislation / bill with unverified result → scaffold or unsupported ──
    if route in ("citation_number", "legislation", "bill") and len(results) == 1:
        item = results[0]
        if not item.get("verified"):
            prefill = build_prefill(route, query, partial=item)
            suggested = SUGGESTED_TYPE_MAP.get(route, route)
            return _scaffold_response(
                route,
                "Could not verify against our databases. Fill in the fields below to generate a McGill 10th citation.",
                prefill=prefill,
                suggested_type=suggested,
                disabled_message=_SCAFFOLD_DISABLED_MSG,
            )

    # ── case_name with multiple candidates → needs_selection ──
    if route == "case_name" and len(results) > 1:
        candidates = []
        for item in results:
            candidates.append({
                "display": _candidate_display(item),
                **item,
            })
        return _envelope(True, route, "needs_selection", {
            "candidates": candidates,
        })

    # ── single result → format directly ──
    try:
        citation = format_citation(results[0])
        debug = _collect_debug_info(route)
        return _envelope(
            True, route, "done",
            {"citations": [{"citation": citation}]},
            debug=debug,
        )
    except Exception as e:
        return _envelope(
            True, route, "error", {},
            error={"reason": f"Formatting failed: {e}"},
        )


def _handle_concept(results: list) -> dict:
    """Handle concept-expansion results with needs_selection for multiple candidates.

    Mirrors the case_name branch: single result → format directly;
    multiple results → return candidates, defer format to /api/citation/select.
    """
    if not results:
        # Concept expansion returned nothing — offer scaffold fallback
        suggested = SUGGESTED_TYPE_MAP.get("concept", "jurisprudence")
        return _scaffold_response(
            "concept",
            "Could not verify against our databases. Fill in the fields below to generate a McGill 10th citation.",
            prefill={"style_of_cause": "duty to consult"},
            suggested_type=suggested,
            disabled_message=_SCAFFOLD_DISABLED_MSG,
        )

    # Multiple candidates → defer format to /api/citation/select
    if len(results) > 1:
        candidates = []
        for item in results:
            candidates.append({
                "display": _candidate_display(item),
                **item,
            })
        return _envelope(True, "concept", "needs_selection", {
            "candidates": candidates,
        })

    # Single result → format directly
    try:
        citation = format_citation(results[0])
        debug = _collect_debug_info("concept")
        return _envelope(
            True, "concept", "done",
            {"citations": [{"citation": citation}]},
            debug=debug,
        )
    except Exception as e:
        return _envelope(
            True, "concept", "error", {},
            error={"reason": f"Formatting failed: {e}"},
        )


# ═══════════════════════════════════════════════════════════════════
#  2. POST /api/citation/select  —  Candidate selection (stateless)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/citation/select")
async def citation_select(body: CitationSelectInput):
    """Format citation from a previously-returned candidate (no server state)."""
    if not body.candidates:
        return _envelope(False, "", "error", {}, error={"reason": "candidates cannot be empty"})
    if body.selected_index < 0 or body.selected_index >= len(body.candidates):
        return _envelope(
            False, "", "error", {},
            error={"reason": f"selected_index {body.selected_index} out of range (0-{len(body.candidates)-1})"},
        )

    cap_block = _check_spend_cap()
    if cap_block:
        return cap_block

    item = body.candidates[body.selected_index]

    try:
        citation = format_citation(item)
        debug = _collect_debug_info("select")
        return _envelope(
            True, "select", "done",
            {"citations": [{"citation": citation}]},
            debug=debug,
        )
    except Exception as e:
        return _envelope(
            True, "select", "error", {},
            error={"reason": f"Formatting failed: {e}"},
        )


# ═══════════════════════════════════════════════════════════════════
#  3. POST /api/extract/file  —  File upload & citation extract
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/extract/file")
async def extract_file(file: UploadFile = File(...)):
    """Upload a document (docx/pdf/pptx/xlsx) and extract McGill citation."""
    # ── Size guard ──
    MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024
    contents = await file.read()
    if len(contents) > MAX_BYTES:
        return _envelope(
            False, "", "error", {},
            error={"reason": f"File too large ({len(contents)/1024/1024:.1f} MB). Maximum is {MAX_UPLOAD_MB} MB"},
        )

    # ── Save to temp file (extract_from_file reads by path) ──
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        cap_block = _check_spend_cap()
        if cap_block:
            return cap_block

        fields = extract_from_file(tmp_path)
        if "error" in fields:
            return _envelope(
                True, "file", "unsupported", {},
                error={"reason": fields["error"]},
            )

        doc_type = classify_document_type(fields.get("raw_text", ""))

        citation = format_citation(fields, doc_type=doc_type)
        debug = _collect_debug_info("file")

        return _envelope(True, "file", "done", {
            "citations": [{"citation": citation}],
            "doc_type": doc_type,
        }, debug=debug)

    except Exception as e:
        return _envelope(
            True, "file", "error", {},
            error={"reason": f"File processing failed: {e}"},
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  4. POST /api/extract/url  —  URL citation extract
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/extract/url")
async def extract_url(body: UrlInput):
    """Extract citation via DOI / ISBN (deterministic) or URL (trafilatura).

    Priority: doi > isbn > url.  Only URL path falls back to manual scaffold.
    Spend tracked at the chokepoint (_call_deepseek / _call_gemini), not here.
    """
    doi = (body.doi or "").strip()
    isbn = (body.isbn or "").strip()
    url = (body.url or "").strip()

    # ── All empty ──
    if not doi and not isbn and not url:
        return _envelope(False, "", "error", {}, error={"reason": "Provide a URL, DOI, or ISBN."})

    cap_block = _check_spend_cap()
    if cap_block:
        return cap_block

    # ── DOI → journal_article (CrossRef deterministic path) ──
    if doi:
        fields = {"raw_text": doi, "url": url or ""}
        try:
            citation = format_citation(fields, doc_type="journal_article")
            debug = _collect_debug_info("url")
            return _envelope(True, "url", "done", {
                "citations": [{"citation": citation}],
            }, debug=debug)
        except ValueError as e:
            msg = str(e)
            # Auto-detect ISBN entered in the DOI field
            if "valid DOI" in msg and extract_isbn(doi):
                isbn = doi  # fall through to ISBN block below
            else:
                return _envelope(
                    True, "url", "unsupported", {},
                    error={"reason": msg},
                )
        except Exception as e:
            return _envelope(
                True, "url", "error", {},
                error={"reason": f"DOI processing failed: {e}"},
            )

    # ── ISBN → book (Open Library deterministic path) ──
    if isbn:
        fields = {"raw_text": isbn}
        try:
            citation = format_citation(fields, doc_type="book")
            debug = _collect_debug_info("url")
            return _envelope(True, "url", "done", {
                "citations": [{"citation": citation}],
            }, debug=debug)
        except ValueError as e:
            msg = str(e)
            if "invalid" in msg:
                return _envelope(
                    True, "url", "error", {},
                    error={"reason": msg},
                )
            return _envelope(
                True, "url", "unsupported", {},
                error={"reason": msg},
            )
        except Exception as e:
            return _envelope(
                True, "url", "error", {},
                error={"reason": f"ISBN processing failed: {e}"},
            )

    # ── URL-only — scaffold or unsupported when extraction fails ──
    fields = extract_from_url(url)
    if "error" in fields:
        error_msg = fields["error"]
        prefill = {"url": url}
        return _scaffold_response(
            "url",
            error_msg,
            prefill=prefill,
            suggested_type="news_online",
            disabled_message=_SCAFFOLD_DISABLED_MSG_URL,
        )

    try:
        citation = format_citation(fields)
        debug = _collect_debug_info("url")

        return _envelope(True, "url", "done", {
            "citations": [{"citation": citation}],
        }, debug=debug)

    except Exception as e:
        return _envelope(
            True, "url", "error", {},
            error={"reason": f"URL processing failed: {e}"},
        )


# ═══════════════════════════════════════════════════════════════════
#  5. POST /api/chat  —  Free-form chat (non-streaming)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def chat(body: ChatInput):
    """Multi-turn chat with DeepSeek (non-streaming)."""
    if not body.messages:
        return _envelope(False, "", "error", {}, error={"reason": "messages cannot be empty"})

    cap_block = _check_spend_cap()
    if cap_block:
        return cap_block

    try:
        messages = [{"role": m.role, "content": m.content} for m in body.messages]
        reply = chat_deepseek(messages)
        return _envelope(True, "chat", "done", {"reply": reply})
    except Exception as e:
        return _envelope(
            True, "chat", "error", {},
            error={"reason": f"Chat failed: {e}"},
        )


# ═══════════════════════════════════════════════════════════════════
#  6. POST /api/feedback  —  User feedback (up/down)
# ═══════════════════════════════════════════════════════════════════

FEEDBACK_FILE = _PROJ / "data" / "feedback.jsonl"


@app.post("/api/feedback")
async def feedback(body: FeedbackInput):
    """Record feedback (rating or message) to local JSONL + HF Dataset + Discord.

    Two delivery paths run in a background executor, each independent:
      1. HF Dataset append (``_persist_feedback_hf``)
      2. Discord webhook notification (``_notify_discord``)
    Neither blocks the HTTP response; each swallows its own exceptions.
    The local JSONL write runs inline for speed — failure there is visible.
    """
    kind = body.kind.strip().lower()
    if kind not in ("rating", "message"):
        return _envelope(False, "", "error", {}, error={"reason": "kind must be 'rating' or 'message'"})

    if kind == "message":
        note = (body.note or "").strip()
        if not note:
            return _envelope(False, "", "error", {}, error={"reason": "note cannot be empty for kind=message"})
        record = {
            "kind": "message",
            "note": note,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }
    else:
        verdict = (body.verdict or "").strip().lower()
        if verdict not in ("up", "down"):
            return _envelope(False, "", "error", {}, error={"reason": "verdict must be 'up' or 'down'"})
        record = {
            "kind": "rating",
            "input": body.input or "",
            "output": body.output or "",
            "route": body.route or "",
            "verdict": verdict,
            "note": body.note or "",
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }

    # ── Local write (fast, always attempted) ──
    try:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        return _envelope(
            True, body.route or "", "error", {},
            error={"reason": f"Feedback save failed: {e}"},
        )

    # ── Background delivery (HF Dataset + Discord) ──
    import asyncio
    asyncio.get_event_loop().run_in_executor(
        None, _deliver_feedback, record,
    )

    return _envelope(True, body.route or "", "done", {})


def _deliver_feedback(record: dict) -> None:
    """Fire-and-forget both delivery paths. Never raises."""
    _persist_feedback_hf(record)
    _notify_discord(record)


def _persist_feedback_hf(record: dict) -> None:
    """Write feedback to HF Dataset in background thread. Never raises."""
    try:
        from core.hf_store import append_record
        ok = append_record(record, filename="feedback.jsonl")
        if not ok:
            logger = __import__("logging").getLogger(__name__)
            logger.warning("Feedback HF Dataset write returned False")
    except Exception:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("Feedback HF Dataset write failed", exc_info=True)


def _notify_discord(record: dict) -> None:
    """Post feedback to Discord webhook in background. Never raises."""
    try:
        from core.discord_notify import notify

        notify(record)
    except Exception:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("Discord notification failed", exc_info=True)


# ═══════════════════════════════════════════════════════════════════
#  7. GET /api/health  —  Health check / wake-up ping
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    """Simple health check (for HF Spaces anti-sleep pings)."""
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════
#  8. GET /api/scaffold/config  —  Scaffold field definitions
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/scaffold/config")
async def scaffold_config():
    """Return all scaffold type options and their field configs.

    Dynamically generated from ``mcgill_rules.json`` at call time.
    Clients may cache; config changes only when the rules JSON is updated.
    """
    return {
        "type_options": get_type_options(),
        "field_configs": get_field_configs(),
    }


# ═══════════════════════════════════════════════════════════════════
#  9. POST /api/citation/assemble  —  Manual scaffold assembly
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/citation/assemble")
async def citation_assemble(body: AssemblyInput):
    """Deterministically assemble a citation from user-supplied fields.

    No LLM, no database lookup.  Pure template substitution.
    Result is always marked *verified: false*.

    Returns unsupported when SCAFFOLD_ENABLED is False.
    """
    if not SCAFFOLD_ENABLED:
        return _envelope(
            True, "", "unsupported", {},
            error={"reason": "Manual citation assembly is currently disabled."},
        )

    configs = get_field_configs()
    if body.type not in configs:
        return _envelope(
            False, body.type, "error", {},
            error={"reason": f"Unknown citation type: {body.type}"},
        )

    try:
        citation = assemble(body.type, body.fields)
        return _envelope(True, body.type, "done", {
            "citations": [{"citation": citation, "verified": False}],
        })
    except Exception as e:
        return _envelope(
            True, body.type, "error", {},
            error={"reason": f"Assembly failed: {e}"},
        )


# ═══════════════════════════════════════════════════════════════════
#  Rate-limit middleware (runs after CORS, before endpoints)
# ═══════════════════════════════════════════════════════════════════

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Free endpoints — no rate limiting
    free_paths = {"/api/health", "/api/feedback", "/api/scaffold/config", "/api/citation/assemble"}
    if request.url.path in free_paths:
        return await call_next(request)

    ip = extract_client_ip(request)
    if not rate_limiter.check(ip):
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "route": "",
                "status": "error",
                "data": {},
                "debug": None,
                "error": {"reason": "Too many requests. Please slow down and retry shortly."},
            },
        )
    return await call_next(request)


