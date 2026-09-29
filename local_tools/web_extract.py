"""Fetch and parse one public web page into citation fields (deterministic, no LLM).

The fetch is guarded against SSRF (``local_tools.url_guard``) and the page is
parsed with trafilatura plus meta tags.
"""

import logging
import requests
from utils.json_util import parse_llm_json

from local_tools.utils import generic_session, request_with_retry

logger = logging.getLogger(__name__)

URL_EXTRACT_FETCH_TIMEOUT = 8

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


def _meta_content(html: str, prop: str) -> str:
    """Return the unescaped content of ``<meta property|name=prop>``, or ""."""
    import html as html_lib
    import re
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(r"""(?:property|name)\s*=\s*["']%s["']""" % re.escape(prop), tag, re.I):
            match = re.search(r"""content\s*=\s*(["'])(.*?)\1""", tag, re.I | re.S)
            if match:
                return html_lib.unescape(match.group(2)).strip()
    return ""


def _best_title(meta_title: str | None, html: str) -> str:
    """Pick between trafilatura's title (usually og:title) and the HTML <title>.

    When one contains the other, the shorter is the article title: the longer
    one carries either a site suffix ("X | Site") or a publisher typo
    ("WhOn Holding's ..." vs "On Holding's ...").
    """
    import html as html_lib
    import re
    meta_title = (meta_title or "").strip()
    match = re.search(r"<title[^>]*>(.*?)</title>", html[:300_000], re.I | re.S)
    page_title = re.sub(r"\s+", " ", html_lib.unescape(match.group(1))).strip() if match else ""
    if meta_title and page_title and meta_title != page_title:
        if page_title in meta_title:
            return page_title
        if meta_title in page_title:
            return meta_title
    return meta_title or page_title


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
        "page_title": _best_title(meta.get("title"), html) or None,
        "site_name": _meta_content(html, "og:site_name") or meta.get("source-hostname") or None,
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
