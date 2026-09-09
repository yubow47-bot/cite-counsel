import os
import json
import time
import logging
import requests
from profiling import timing
from utils.json_util import parse_llm_json

from local_tools.utils import deepseek_session, generic_session, request_with_retry

logger = logging.getLogger(__name__)


def _load_env():
    """Load .env file for API key."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


# Must run BEFORE the constants below read os.getenv — this ordering bug
# silently ignored .env-provided LLM_* values at import time.
_load_env()


# ── Provider selection ──────────────────────────────────────────────────────
# Default: DeepSeek direct.  Set LLM_COMPLETIONS_URL to any OpenAI-compatible
# chat/completions endpoint (e.g. https://openrouter.ai/api/v1/chat/completions)
# and provide OPENROUTER_API_KEY (or LLM_API_KEY) to route the SAME pipeline
# through it; LLM_DEFAULT_MODEL then names the provider's model id.
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
COMPLETIONS_URL = os.getenv("LLM_COMPLETIONS_URL", DEEPSEEK_API_URL)
DEEPSEEK_MODEL = os.getenv("LLM_DEFAULT_MODEL", "deepseek-v4-flash")
URL_EXTRACT_FETCH_TIMEOUT = 8

# ── Track first call (DNS + TLS setup on new connection) ──
_first_deepseek_http = True


def _mark_first_deepseek_http() -> bool:
    global _first_deepseek_http
    if _first_deepseek_http:
        _first_deepseek_http = False
        return True
    return False


def _get_api_key() -> str:
    """Resolve the API key for the configured completions endpoint.

    DeepSeek direct -> DEEPSEEK_API_KEY.  Any custom endpoint (OpenRouter,
    etc.) -> OPENROUTER_API_KEY, falling back to LLM_API_KEY.
    """
    if COMPLETIONS_URL != DEEPSEEK_API_URL:
        key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY") or ""
        if not key:
            raise ValueError(
                "LLM_COMPLETIONS_URL is set but no key found. "
                "Set OPENROUTER_API_KEY (or LLM_API_KEY) in the environment."
            )
        return key
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError(
            "DEEPSEEK_API_KEY not configured. "
            "Set it in the .env file: DEEPSEEK_API_KEY=sk-your-key-here"
        )
    return key


def _call_deepseek(messages: list, temperature: float = 0, model: str | None = None, disable_thinking: bool = False) -> str:
    """Internal: call DeepSeek API with messages, return response text."""
    _http_t0 = time.perf_counter()
    _is_first = _mark_first_deepseek_http()

    api_key = _get_api_key()  # raises a clean ValueError when unconfigured
    actual_model = model or DEEPSEEK_MODEL

    if _is_first:
        logger.debug("[DUR] DeepSeek HTTP — FIRST request (cold DNS + TCP + TLS)")

    body: dict = {
        "model": actual_model,
        "messages": messages,
        "temperature": temperature,
    }
    if disable_thinking and COMPLETIONS_URL == DEEPSEEK_API_URL:
        # DeepSeek-specific extension — other OpenAI-compatible endpoints
        # (OpenRouter upstreams) reject unknown params.
        body["thinking"] = {"type": "disabled"}

    with timing.measure("http.deepseek", model=actual_model):
        response = request_with_retry(
            deepseek_session, "POST",
            COMPLETIONS_URL,
            # retries=0: POST is not idempotent — a read-timeout retry could
            # duplicate a completed LLM call and double-charge.
            retries=0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            read_timeout=30,
        )
    _http_elapsed = time.perf_counter() - _http_t0
    logger.debug("[DUR] DeepSeek _call_deepseek HTTP — %.1fms  first=%s", _http_elapsed * 1000, _is_first)
    response.raise_for_status()
    data = response.json()

    # ── Track token spend ──
    try:
        usage = data.get("usage", {})
        in_tokens = usage.get("prompt_tokens", 0)
        out_tokens = usage.get("completion_tokens", 0)
        if in_tokens or out_tokens:
            from core.spend_tracker import spend_tracker
            spend_tracker.record_cost("deepseek", actual_model, in_tokens, out_tokens)
    except Exception as e:
        logger.warning("DeepSeek spend tracking failed: %s", e)

    return data["choices"][0]["message"]["content"]


def ask_deepseek(prompt: str, model: str | None = None, disable_thinking: bool = False) -> str:
    """Single-turn prompt."""
    return _call_deepseek([{"role": "user", "content": prompt}], model=model, disable_thinking=disable_thinking)


def chat_deepseek(messages: list) -> str:
    """Multi-turn chat, replaces chat_ollama."""
    return _call_deepseek(messages, temperature=0.7)


_REDIRECT_STATUS = (301, 302, 303, 307, 308)
_MAX_REDIRECT_HOPS = 5


def fetch_html(url: str, timeout: int = 15) -> str | None:
    """Fetch HTML from a user-supplied URL (SSRF-guarded).

    curl_cffi first (Chrome TLS fingerprint), plain requests fallback —
    curl_cffi with impersonate="chrome" sometimes times out on sites that
    respond fine to a plain requests.get() with a standard User-Agent, and
    the fallback catches that case.

    Redirects are followed MANUALLY (allow_redirects=False) so every hop is
    re-validated against the internal-address blocklist in
    local_tools.url_guard before the next request — an external page can no
    longer 302 the server into fetching its own loopback / private network.
    Response bodies are size-capped.  Returns None on any failure (blocked,
    unreachable, oversized, too many hops).
    """
    from local_tools.url_guard import (
        MAX_RESPONSE_BYTES,
        UrlBlocked,
        next_redirect_url,
        validate_url,
    )

    import curl_cffi.requests as cffi_requests

    try:
        current = validate_url(url)
    except UrlBlocked as e:
        logger.info("[SSRF] URL fetch blocked: %s", e)
        return None

    def _capped(text: str | None) -> str | None:
        # Post-hoc body cap: trafilatura only needs a normal article; a body
        # beyond MAX_RESPONSE_BYTES is not a citation source (memory spike
        # before the cap is bounded by the fetch timeout).
        if text and len(text) > MAX_RESPONSE_BYTES:
            return None
        return text

    # ── Primary attempt: curl_cffi (Chrome TLS fingerprint) ──
    try:
        hops = 0
        while True:
            r = cffi_requests.get(
                current, impersonate="chrome", timeout=timeout, allow_redirects=False,
            )
            if r.status_code in _REDIRECT_STATUS:
                if hops >= _MAX_REDIRECT_HOPS:
                    return None
                current = next_redirect_url(current, r.headers.get("location", ""))
                hops += 1
                continue
            r.raise_for_status()
            return _capped(r.text)
    except UrlBlocked as e:
        logger.info("[SSRF] redirect blocked: %s", e)
        return None
    except Exception:
        pass

    # ── Fallback: plain requests with standard browser User-Agent ──
    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    try:
        hops = 0
        while True:
            resp = request_with_retry(
                generic_session, "GET", current,
                connect_timeout=2.7, read_timeout=5, retries=0,
                headers={"User-Agent": _UA},
                allow_redirects=False,
            )
            if resp.status_code in _REDIRECT_STATUS:
                if hops >= _MAX_REDIRECT_HOPS:
                    return None
                current = next_redirect_url(current, resp.headers.get("location", ""))
                hops += 1
                continue
            try:
                resp.raise_for_status()
                return _capped(resp.text)
            except requests.exceptions.HTTPError:
                return None
            finally:
                resp.close()
    except UrlBlocked as e:
        logger.info("[SSRF] redirect blocked (fallback): %s", e)
        return None
    except Exception:
        return None


def extract_url(url: str) -> str | None:
    """抓取 + 解析。任一步失败返回 None，交给上层降级。"""
    html = fetch_html(url, timeout=URL_EXTRACT_FETCH_TIMEOUT)
    if not html:
        return None
    import trafilatura
    return trafilatura.extract(html, include_comments=False)


def extract_from_url(url: str) -> dict:
    """Fetch a URL with curl_cffi and extract structured citation fields.

    Uses trafilatura's JSON output to get title, author, date, and sitename
    directly from the page metadata, without needing DeepSeek for extraction.
    Returns a dict with ``"error"`` key on failure so the caller can degrade
    to the manual scaffold.
    """
    # ── PDF URL short-circuit ──
    # We have no PDF text/byte parser in this module, so a .pdf-suffixed URL
    # can never produce correct document data.  Return immediately before any
    # network call to avoid silently returning archive-interstitial metadata.
    _path = url.split("?", 1)[0]
    if _path.lower().endswith(".pdf"):
        return {"url": url, "error": "We can't reliably read a direct PDF link. Please fill in the citation fields manually."}

    import trafilatura

    html = fetch_html(url, timeout=URL_EXTRACT_FETCH_TIMEOUT)
    if not html:
        return {"url": url, "error": "We couldn't fetch this page. It may be blocking automated access, or the request may have timed out. Please fill in the citation fields manually."}

    result = None
    try:
        result = trafilatura.extract(
            html,
            output_format="json",
            with_metadata=True,
            include_comments=False,
        )
        if result is None:
            return {"url": url, "error": "trafilatura could not extract content from this page"}
        meta = parse_llm_json(result)
    except Exception as e:
        logger.warning("[JSON] extract_from_url failed: %s  len=%d", e, len(result) if result else 0)
        return {"url": url, "error": "We couldn't read the content of this page. Try uploading a screenshot instead."}

    # 站点标识 = 小写裸域（规则示例 online: <cigionline.org> 的形态）。
    # 域名不是刊名——不再编造全大写 newspaper。
    hostname = meta.get("hostname", "") or ""

    fields = {
        "url": url,
        "page_title": meta.get("title") or None,
        "author": meta.get("author") or None,
        "date": meta.get("date") or None,
        "site_domain": hostname or None,
        "hostname": hostname,
        "raw_text": meta.get("raw_text") or "",
        "style_of_cause": None,
        "neutral_citation": None,
        "statute_title": None,
        "jurisdiction": None,
        "year": (meta.get("date") or "")[:4] or None,
    }

    return fields
