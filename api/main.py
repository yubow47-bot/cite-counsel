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
from api.spend_tracker import tracker, BudgetExceeded
from api.rate_limiter import RateLimiter
from api.scaffold import (
    assemble,
    build_prefill,
    get_type_options,
    get_field_configs,
    SCAFFOLD_ELIGIBLE_ROUTES,
    SUGGESTED_TYPE_MAP,
)

# ── Existing pipeline imports (no changes to these modules) ──
from local_tools.citation_search import classify_and_normalize, search_citation
from local_tools.file_extractor import extract_from_file, classify_document_type
from llm_api.deepseek_api import extract_from_url, chat_deepseek
from core.mcgill_engine import format_citation, get_last_debug, detect_type, get_rules

# ═══════════════════════════════════════════════════════════════════
#  App setup
# ═══════════════════════════════════════════════════════════════════

DEBUG = os.getenv("DEBUG_RESPONSES", "false").lower() in ("1", "true", "yes")
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    input: str
    output: str
    route: str
    verdict: str  # "up" or "down"
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


def _check_budget():
    """Raise BudgetExceeded if daily DeepSeek limit reached."""
    ok, reason = tracker.check()
    if not ok:
        raise BudgetExceeded(reason)


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


# ═══════════════════════════════════════════════════════════════════
#  1. POST /api/citation  —  Citation query (hero flow)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/citation")
async def citation_query(body: CitationInput, request: Request):
    """Search legal citations.  Routes to direct format or candidate selection."""
    if not body.input or not body.input.strip():
        return _envelope(False, "", "error", {}, error={"reason": "Input cannot be empty"})

    query = body.input.strip()
    _check_budget()

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
            return _envelope(True, route, "needs_input", {
                "message": (
                    "Could not verify against our databases. "
                    "Fill in the fields below to generate a McGill 10th citation."
                ),
                "prefill": prefill,
                "type": suggested,
            })
        return _envelope(
            True, route, "unsupported",
            {}, error={"reason": "No matching results found. Try different keywords."},
        )

    # ── concept: multi-result, format each ──
    if route == "concept":
        return _handle_concept(results)

    # ── citation_number / legislation / bill with unverified result → needs_input ──
    if route in ("citation_number", "legislation", "bill") and len(results) == 1:
        item = results[0]
        if not item.get("verified"):
            prefill = build_prefill(route, query, partial=item)
            suggested = SUGGESTED_TYPE_MAP.get(route, route)
            return _envelope(True, route, "needs_input", {
                "message": (
                    "Could not verify against our databases. "
                    "Fill in the fields below to generate a McGill 10th citation."
                ),
                "prefill": prefill,
                "type": suggested,
            })

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
        tracker.increment()
        debug = _collect_debug_info(route)
        return _envelope(
            True, route, "done",
            {"citations": [{"citation": citation}]},
            debug=debug,
        )
    except BudgetExceeded:
        raise
    except Exception as e:
        return _envelope(
            True, route, "error", {},
            error={"reason": f"Formatting failed: {e}"},
        )


def _handle_concept(results: list) -> dict:
    """Format each concept-expansion result individually."""
    if not results:
        # Concept expansion returned nothing — offer scaffold fallback
        suggested = SUGGESTED_TYPE_MAP.get("concept", "jurisprudence")
        return _envelope(True, "concept", "needs_input", {
            "message": (
                "Could not verify against our databases. "
                "Fill in the fields below to generate a McGill 10th citation."
            ),
            "prefill": {"style_of_cause": "duty to consult"},
            "type": suggested,
        })
    citations = []
    for item in results:
        source = (
            item.get("style_of_cause")
            or item.get("statute_title")
            or item.get("name", "")
        )
        try:
            cit = format_citation(item)
            citations.append({"citation": cit, "source_case": source})
            tracker.increment()  # each format may call LLM
        except BudgetExceeded:
            raise
        except Exception as e:
            citations.append({"citation": f"[Formatting failed: {e}]", "source_case": source})
    debug = _collect_debug_info("concept")
    return _envelope(True, "concept", "done", {"citations": citations}, debug=debug)


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

    _check_budget()
    item = body.candidates[body.selected_index]

    try:
        citation = format_citation(item)
        tracker.increment()
        debug = _collect_debug_info("select")
        return _envelope(
            True, "select", "done",
            {"citations": [{"citation": citation}]},
            debug=debug,
        )
    except BudgetExceeded:
        raise
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
        _check_budget()
        fields = extract_from_file(tmp_path)
        doc_type = classify_document_type(fields.get("raw_text", ""))
        tracker.increment()  # classify_document_type calls DeepSeek

        citation = format_citation(fields, doc_type=doc_type)
        tracker.increment()  # format_citation may call DeepSeek
        debug = _collect_debug_info("file")

        return _envelope(True, "file", "done", {
            "citations": [{"citation": citation}],
            "doc_type": doc_type,
        }, debug=debug)

    except BudgetExceeded:
        raise
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
    tracker.increment() only for DeepSeek calls (deterministic hits skip it).
    """
    doi = (body.doi or "").strip()
    isbn = (body.isbn or "").strip()
    url = (body.url or "").strip()

    # ── All empty ──
    if not doi and not isbn and not url:
        return _envelope(False, "", "error", {}, error={"reason": "Provide a URL, DOI, or ISBN."})

    _check_budget()

    # ── DOI → journal_article (CrossRef deterministic path) ──
    if doi:
        fields = {"raw_text": doi, "url": url or ""}
        try:
            citation = format_citation(fields, doc_type="journal_article")
            src = get_last_debug().get("source", "")
            if src in ("deepseek", "deepseek_fallback"):
                tracker.increment()
            debug = _collect_debug_info("url")
            return _envelope(True, "url", "done", {
                "citations": [{"citation": citation}],
            }, debug=debug)
        except BudgetExceeded:
            raise
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
            src = get_last_debug().get("source", "")
            if src in ("deepseek", "deepseek_fallback"):
                tracker.increment()
            debug = _collect_debug_info("url")
            return _envelope(True, "url", "done", {
                "citations": [{"citation": citation}],
            }, debug=debug)
        except BudgetExceeded:
            raise
        except Exception as e:
            return _envelope(
                True, "url", "error", {},
                error={"reason": f"ISBN processing failed: {e}"},
            )

    # ── URL-only (existing behaviour, unchanged) ──
    fields = extract_from_url(url)
    if "error" in fields:
        error_msg = fields["error"]
        prefill = {"url": url}
        return _envelope(True, "url", "needs_input", {
            "message": error_msg,
            "prefill": prefill,
            "type": "news_online",
        })

    try:
        citation = format_citation(fields)
        src = get_last_debug().get("source", "")
        if src in ("deepseek", "deepseek_fallback"):
            tracker.increment()
        debug = _collect_debug_info("url")

        return _envelope(True, "url", "done", {
            "citations": [{"citation": citation}],
        }, debug=debug)

    except BudgetExceeded:
        raise
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

    _check_budget()

    try:
        messages = [{"role": m.role, "content": m.content} for m in body.messages]
        reply = chat_deepseek(messages)
        tracker.increment()
        return _envelope(True, "chat", "done", {"reply": reply})
    except BudgetExceeded:
        raise
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
    """Record user feedback (up/down) to data/feedback.jsonl.

    Verdict must be "up" or "down".  Payload appended as JSONL for later
    upload to HF Dataset.
    """
    verdict = body.verdict.strip().lower()
    if verdict not in ("up", "down"):
        return _envelope(False, "", "error", {}, error={"reason": "verdict must be 'up' or 'down'"})

    record = {
        "input": body.input,
        "output": body.output,
        "route": body.route,
        "verdict": verdict,
        "note": body.note or "",
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }

    try:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return _envelope(True, body.route, "done", {})
    except Exception as e:
        return _envelope(
            True, body.route, "error", {},
            error={"reason": f"Feedback save failed: {e}"},
        )


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
    """
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
    # Skip rate limiting for health check
    if request.url.path == "/api/health":
        return await call_next(request)

    ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(ip):
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "route": "",
                "status": "error",
                "data": {},
                "debug": None,
                "error": {"reason": "Too many requests. Please try again later."},
            },
        )
    return await call_next(request)


# ═══════════════════════════════════════════════════════════════════
#  Budget-exceeded handler
# ═══════════════════════════════════════════════════════════════════

@app.exception_handler(BudgetExceeded)
async def budget_exceeded_handler(request: Request, exc: BudgetExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "ok": False,
            "route": "",
            "status": "error",
            "data": {},
            "debug": None,
            "error": {"reason": str(exc)},
        },
    )
