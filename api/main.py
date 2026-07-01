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
from local_tools.utils import (
    legisinfo_session,
    a2aj_session,
    canlii_session,
    crossref_session,
    openlibrary_session,
    deepseek_session,
    gemini_session,
    request_with_retry,
)

# ═══════════════════════════════════════════════════════════════════
#  App setup
# ═══════════════════════════════════════════════════════════════════

DEBUG = os.getenv("DEBUG_RESPONSES", "false").lower() in ("1", "true", "yes")
SCAFFOLD_ENABLED = os.getenv("SCAFFOLD_ENABLED", "false").lower() in ("1", "true", "yes")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
EMPTY_BODY_THRESHOLD = 50  # chars — below this, treat the extracted body as unusable
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]


_USER_FACING_ERROR = "We couldn't process this request. Please try again in a moment."


def _without_internal(d: dict) -> dict:
    """Strip known internal diagnostic keys from a result dict before user-facing use.

    Only specific keys (``_match_path``) are removed.  Other underscore-prefixed
    keys (e.g. ``_bill_citation`` used by the bill route) are preserved.
    """
    _INTERNAL_KEYS = {"_match_path"}
    return {k: v for k, v in d.items() if k not in _INTERNAL_KEYS}


app = FastAPI(title="McGill Citation Tool API", version="1.0.0")

# ── Startup marker: distinguishes brand-new container from warm reuse ──
import datetime as _dt
import logging as _logging
_logger = _logging.getLogger(__name__)
_CONTAINER_START_TS = _dt.datetime.now()
_logger.info("[STARTUP] %s — Container/process started, app boot complete", _CONTAINER_START_TS.isoformat())

# ── Per-process lazy-init tracking ──
_FIRST_REQUEST = True  # reset to True each time the process starts


def _mark_first_request() -> bool:
    """Return True if this is the first request since process boot."""
    global _FIRST_REQUEST
    if _FIRST_REQUEST:
        _FIRST_REQUEST = False
        return True
    return False


rate_limiter = RateLimiter()

# ── LEGISinfo cache warm-up at boot ─────────────────────────────────────
# Pre-fetch the current-session bill list so the module-level _CACHE is
# populated when the first bill query arrives.  Failures are logged but do
# NOT block app startup — the existing lazy-fetch-on-miss is the fallback.
@app.on_event("startup")
async def _warm_legisinfo_cache():
    try:
        from local_tools.legisinfo_api import fetch_legisinfo_bills
        bills = fetch_legisinfo_bills(force_refresh=True)
        _logger.info("[STARTUP] LEGISinfo cache warmed — %d bills loaded in current session", len(bills))
    except Exception as exc:
        _logger.warning("[STARTUP] LEGISinfo cache warm-up failed (non-fatal): %s", exc)

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
    cit = item.get("citation") or item.get("neutral_citation") or item.get("chapter") or item.get("reporter", "")
    pin = item.get("pinpoint")
    # Bill candidates carry session and title for disambiguation
    bill_session = item.get("bill_session", "")
    bill_title = item.get("bill_title", "")
    parts = [f"{badge} {name}"]
    if bill_session:
        parts.append(f"Session {bill_session}")
    if bill_title:
        parts.append(bill_title[:80])
    elif cit:
        parts.append(f"{cit}, {pin}" if pin else cit)
    elif pin:
        parts.append(pin)
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
    Always includes ``suggested_type`` in data so the frontend can default the
    manual scaffold form to the correct source type.
    """
    if SCAFFOLD_ENABLED:
        data: dict = {"message": message}
        if prefill:
            data["prefill"] = prefill
        if suggested_type:
            data["type"] = suggested_type
        return _envelope(True, route, "needs_input", data)

    data: dict = {}
    if suggested_type:
        data["type"] = suggested_type
    return _envelope(
        True, route, "unsupported", data,
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
        _logger.warning("Classification failed: %s", e)
        return _envelope(False, "", "error", {}, error={"reason": _USER_FACING_ERROR})

    route = classified["type"]

    # ── Step 2: search ──
    try:
        results = search_citation(query, classification=classified)
    except Exception as e:
        _logger.warning("Search failed: %s", e)
        return _envelope(
            False, route, "error", {}, error={"reason": _USER_FACING_ERROR}
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

    # ── bill / case_name / legislation with multiple candidates → needs_selection ──
    if route in ("bill", "case_name", "legislation") and len(results) > 1:
        candidates = []
        for item in results:
            candidates.append({
                "display": _candidate_display(item),
                **_without_internal(item),
            })
        return _envelope(True, route, "needs_selection", {
            "candidates": candidates,
        })

    # ── single result → format directly ──
    try:
        _pin = results[0].get("pinpoint")
        # For case route: strip pinpoint from format_citation input (composed client-side,
        # never reaches extracted_fields / LLM prompt).  Legislation/constitutional routes
        # continue to pass pinpoint through (the LLM includes it in the citation text).
        if route == "case_name" and _pin:
            fmt_item = {k: v for k, v in results[0].items() if k != "pinpoint"}
            citation = format_citation(_without_internal(fmt_item))
        else:
            citation = format_citation(_without_internal(results[0]))
        _cit_data: dict = {"citation": citation, "source_type": route}
        if _pin:
            _cit_data["pinpoint"] = _pin
        debug = _collect_debug_info(route)
        return _envelope(
            True, route, "done",
            {"citations": [_cit_data]},
            debug=debug,
        )
    except Exception as e:
        _logger.warning("Formatting failed for route=%s: %s", route, e)
        return _envelope(
            True, route, "error", {},
            error={"reason": _USER_FACING_ERROR},
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
                **_without_internal(item),
            })
        return _envelope(True, "concept", "needs_selection", {
            "candidates": candidates,
        })

    # Single result → format directly
    try:
        citation = format_citation(_without_internal(results[0]))
        _cit_data: dict = {"citation": citation, "source_type": "concept"}
        _pin = results[0].get("pinpoint")
        if _pin:
            _cit_data["pinpoint"] = _pin
        debug = _collect_debug_info("concept")
        return _envelope(
            True, "concept", "done",
            {"citations": [_cit_data]},
            debug=debug,
        )
    except Exception as e:
        _logger.warning("Formatting failed for concept: %s", e)
        return _envelope(
            True, "concept", "error", {},
            error={"reason": _USER_FACING_ERROR},
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
        # Strip pinpoint before format_citation (composed client-side)
        _pin = item.get("pinpoint")
        if _pin:
            fmt_item = {k: v for k, v in item.items() if k != "pinpoint"}
            citation = format_citation(_without_internal(fmt_item))
        else:
            citation = format_citation(_without_internal(item))
        # Bill candidates carry bill_session → use "bill" directly;
        # detect_type cannot classify LEGISinfo record keys.
        item_source_type = "bill" if item.get("bill_session") else detect_type(item)
        _cit_data: dict = {"citation": citation, "source_type": item_source_type}
        if _pin:
            _cit_data["pinpoint"] = _pin
        debug = _collect_debug_info("select")
        return _envelope(
            True, "select", "done",
            {"citations": [_cit_data]},
            debug=debug,
        )
    except Exception as e:
        _logger.warning("Formatting failed for select: %s", e)
        return _envelope(
            True, "select", "error", {},
            error={"reason": _USER_FACING_ERROR},
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
            "citations": [{"citation": citation, "source_type": doc_type}],
            "doc_type": doc_type,
        }, debug=debug)

    except Exception as e:
        _logger.warning("File processing failed: %s", e)
        return _envelope(
            True, "file", "error", {},
            error={"reason": _USER_FACING_ERROR},
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
                "citations": [{"citation": citation, "source_type": "journal_article"}],
            }, debug=debug)
        except ValueError as e:
            msg = str(e)
            _logger.warning("DOI processing failed: %s", e)
            # Auto-detect ISBN entered in the DOI field
            if "valid DOI" in msg and extract_isbn(doi):
                isbn = doi  # fall through to ISBN block below
            else:
                return _envelope(
                    True, "url", "unsupported", {},
                    error={"reason": "We couldn't process this DOI. Please try again or enter the details manually."},
                )
        except Exception as e:
            _logger.warning("DOI processing failed: %s", e)
            return _envelope(
                True, "url", "error", {},
                error={"reason": _USER_FACING_ERROR},
            )

    # ── ISBN → book (Open Library deterministic path) ──
    if isbn:
        fields = {"raw_text": isbn}
        try:
            citation = format_citation(fields, doc_type="book")
            debug = _collect_debug_info("url")
            return _envelope(True, "url", "done", {
                "citations": [{"citation": citation, "source_type": "book"}],
            }, debug=debug)
        except ValueError as e:
            msg = str(e)
            _logger.warning("ISBN processing failed: %s", e)
            if "invalid" in msg:
                return _envelope(
                    True, "url", "error", {},
                    error={"reason": "We couldn't process this ISBN. Please check the number and try again."},
                )
            return _envelope(
                True, "url", "unsupported", {},
                error={"reason": "We couldn't process this ISBN. Please try again or enter the details manually."},
            )
        except Exception as e:
            _logger.warning("ISBN processing failed: %s", e)
            return _envelope(
                True, "url", "error", {},
                error={"reason": _USER_FACING_ERROR},
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

    # ── Empty-body guard: trafilatura may return metadata but no body
    #     for JS-rendered pages (ourcommons.ca, etc.).  Under 50 chars
    #     the extracted body is unusable — degrade gracefully to the
    #     screenshot / manual path instead of emitting a wrong citation.
    raw_text = fields.get("raw_text", "") or ""
    if len(raw_text.strip()) < EMPTY_BODY_THRESHOLD:
        return _envelope(
            True, "url", "unsupported", {},
            error={"reason": (
                "We couldn't read the main content of this page. "
                "Some sites (especially government sites) load their "
                "content with JavaScript, which our URL reader can't "
                "capture. Try uploading a screenshot of the page instead "
                "(use the File Extraction tab), or enter the details "
                "manually."
            )},
        )

    try:
        doc_type = classify_document_type(raw_text)
        citation = format_citation(fields, doc_type=doc_type)
        debug = _collect_debug_info("url")

        return _envelope(True, "url", "done", {
            "citations": [{"citation": citation, "source_type": doc_type}],
        }, debug=debug)

    except Exception as e:
        _logger.warning("URL processing failed: %s", e)
        return _envelope(
            True, "url", "error", {},
            error={"reason": _USER_FACING_ERROR},
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
        _logger.warning("Chat failed: %s", e)
        return _envelope(
            True, "chat", "error", {},
            error={"reason": _USER_FACING_ERROR},
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
        _logger.warning("Feedback save failed: %s", e)
        return _envelope(
            True, body.route or "", "error", {},
            error={"reason": _USER_FACING_ERROR},
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
#  7. GET /api/warmup  —  Lightweight connection warm-up (page load)
# ═══════════════════════════════════════════════════════════════════

_WARMUP_TARGETS: list[tuple[str, object, str]] = [
    ("legisinfo",  legisinfo_session,  "https://www.parl.ca"),
    ("a2aj",       a2aj_session,       "https://api.a2aj.ca"),
    ("canlii",     canlii_session,     "https://api.canlii.org/v1"),
    ("crossref",   crossref_session,   "https://api.crossref.org"),
    ("openlibrary", openlibrary_session, "https://openlibrary.org"),
    ("deepseek",   deepseek_session,   "https://api.deepseek.com"),
    ("gemini",     gemini_session,     "https://generativelanguage.googleapis.com"),
]

# ── Process-level cooldown: the endpoint warms a shared process-wide
#    connection pool, so a process-wide cooldown is the correct mechanism
#    (not per-IP rate limiting, which would be wrong for this use case).
_last_warmup_result: dict | None = None
_last_warmup_ts: float = 0.0
_WARMUP_COOLDOWN_SECONDS = 120


@app.get("/api/warmup")
async def warmup():
    """Ping all provider endpoints to warm connection pools (cold-start
    mitigation for serverless / auto-scaling deployments).

    Each provider is tried independently — one failure does not block the
    others.  Uses ``retries=0`` (single attempt) and a 3-second read timeout
    to stay lightweight.  Always returns 200.

    Cached for ``_WARMUP_COOLDOWN_SECONDS`` at process level — subsequent
    calls within that window return the previous result without re-probing.
    """
    import asyncio
    import time as _time

    global _last_warmup_result, _last_warmup_ts

    now = _time.time()
    if _last_warmup_result is not None and now - _last_warmup_ts < _WARMUP_COOLDOWN_SECONDS:
        return _last_warmup_result

    async def _probe(name: str, session, url: str) -> str | None:
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: request_with_retry(
                    session, "GET", url, retries=0, read_timeout=3,
                ),
            )
            # Consume the response to release the connection back to the pool
            resp.content
            resp.close()
            return None  # success
        except Exception as exc:
            _logger.warning("Warmup probe failed for %s (%s): %s", name, url, exc)
            return "warmup_failed"  # generic marker, never raw exception text

    warmed: list[str] = []
    failed: list[dict] = []
    results = await asyncio.gather(
        *(_probe(n, s, u) for n, s, u in _WARMUP_TARGETS),
        return_exceptions=False,
    )
    for (name, _, _), marker in zip(_WARMUP_TARGETS, results):
        if marker is None:
            warmed.append(name)
        else:
            failed.append({"provider": name, "reason": marker})

    result = {"warmed": warmed, "failed": failed}
    _last_warmup_result = result
    _last_warmup_ts = now
    return result


# ═══════════════════════════════════════════════════════════════════
#  8. GET /api/health  —  Health check / wake-up ping
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
            "citations": [{"citation": citation, "verified": False, "source_type": body.type}],
        })
    except Exception as e:
        _logger.warning("Assembly failed for type=%s: %s", body.type, e)
        return _envelope(
            True, body.type, "error", {},
            error={"reason": _USER_FACING_ERROR},
        )


# ═══════════════════════════════════════════════════════════════════
#  Timing & first-request logging middleware (outermost — wraps all handlers)
# ═══════════════════════════════════════════════════════════════════

import time as _time

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    _t0 = _time.perf_counter()
    _is_first = _mark_first_request()
    response = await call_next(request)
    _elapsed = _time.perf_counter() - _t0
    _first_tag = " [FIRST-REQUEST]" if _is_first else ""
    _logger.debug("[TIMING] %s %s — %.1fms%s", request.method, request.url.path, _elapsed * 1000, _first_tag)
    return response


# ═══════════════════════════════════════════════════════════════════
#  Rate-limit middleware (runs after CORS, before endpoints)
# ═══════════════════════════════════════════════════════════════════

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Free endpoints — no rate limiting
    free_paths = {"/api/health", "/api/warmup", "/api/feedback", "/api/scaffold/config", "/api/citation/assemble"}
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


