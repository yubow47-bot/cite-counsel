import os
import re
import time
import logging

import requests

from local_tools import timing_util as timing
from local_tools.utils import canlii_session, request_with_retry
from profiling import timing as prof

logger = logging.getLogger(__name__)

CANLII_BASE = "https://api.canlii.org/v1"

# ── Track first HTTP call to CanLII ──
_first_canlii_call = True


def _mark_first_canlii() -> bool:
    global _first_canlii_call
    if _first_canlii_call:
        _first_canlii_call = False
        return True
    return False


def get_case_databases(language: str = "en") -> dict:
    """获取 CanLII 判例数据库列表。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        t0 = time.time()
        with prof.measure("http.canlii_case_databases", endpoint="/caseBrowse/{language}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/caseBrowse/{language}/",
                params={"api_key": key},
                read_timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"get_case_databases({language})", time.time() - t0)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII get_case_databases failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}


def get_case_metadata(
    database_id: str,
    case_id: str,
    language: str = "en",
) -> dict:
    """获取单个判例的元数据。

    Args:
        database_id: CanLII 数据库 ID。
        case_id: 判例 ID。
        language: "en" 或 "fr"。

    返回原始 JSON，不做字段映射。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    if not database_id:
        return {"error": "database_id required"}
    if not database_id.strip():
        return {"error": "database_id required"}
    if not case_id:
        return {"error": "case_id required"}
    if not case_id.strip():
        return {"error": "case_id required"}

    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        t0 = time.time()
        with prof.measure("http.canlii_case_metadata", endpoint="/caseBrowse/{language}/{database_id}/{case_id}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/caseBrowse/{language}/{database_id}/{case_id}/",
                params={"api_key": key},
                read_timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"get_case_metadata({database_id}/{case_id})",
                time.time() - t0,
            )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII get_case_metadata failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}


# ── Citation → caseId resolution ─────────────────────────────────────────────
# A CanLII case id is its neutral citation with the spaces removed and
# lower-cased: "2012 CanLII 27167" → "2012canlii27167", "2012 SCC 13" →
# "2012scc13".  The database id in the request path is NOT used for lookup —
# the API resolves the case from the id alone and reports the real database in
# the response — so a citation can be resolved without knowing its court.
_NEUTRAL_CITATION_RE = re.compile(r"^(\d{4})\s+([A-Za-z]{2,10})\s+(\d+)$")

# "(CanLII)", "(NL SC)", "(SCC)" — the court/publisher tag CanLII appends.
_CITATION_TAG_RE = re.compile(r"\s*\([^)]*\)\s*$")


def citation_to_case_id(citation: str) -> str | None:
    """``"2012 CanLII 27167"`` → ``"2012canlii27167"``; None if not neutral-shaped."""
    m = _NEUTRAL_CITATION_RE.match((citation or "").strip())
    if not m:
        return None
    return f"{m.group(1)}{m.group(2).lower()}{m.group(3)}"


def _split_canlii_citation(citation: str) -> tuple[str, str]:
    """Split a CanLII ``citation`` field into (neutral, parallel reporter).

    "2012 SCC 13 (CanLII), [2012] 1 SCR 433"  → ("2012 SCC 13", "[2012] 1 SCR 433")
    "2012 CanLII 27167 (NL SC)"               → ("2012 CanLII 27167", "")
    "1959 CanLII 50 (SCC), [1959] SCR 121"    → ("1959 CanLII 50", "[1959] SCR 121")
    """
    if not citation:
        return ("", "")
    head, _, tail = citation.partition(",")
    neutral = _CITATION_TAG_RE.sub("", head).strip()
    reporter = _CITATION_TAG_RE.sub("", tail).strip().rstrip(",").strip()
    return (neutral, reporter)


def fetch_case_by_citation(citation: str) -> dict:
    """Resolve a case by neutral citation through CanLII, for citations A2AJ lacks.

    Fills the gap A2AJ leaves: CanLII-assigned numbers ("2012 CanLII 27167", used
    for decisions with no court-assigned neutral citation) and provincial courts
    outside A2AJ's coverage.

    Returns the same field shape as ``a2aj_api._map_fields`` for a case, or
    ``{"raw_input": citation}`` when the citation does not resolve.

    Citation slotting follows McGill: a CanLII-ASSIGNED number ("1959 CanLII 50")
    is a last-resort identifier, so when CanLII also reports an official reporter
    the reporter is cited alone.  A COURT-assigned neutral citation ("2012 SCC
    13") is the preferred first citation and keeps the reporter as a parallel.
    """
    case_id = citation_to_case_id(citation)
    if not case_id:
        return {"raw_input": citation}

    # databaseId is ignored by the API for id lookups; "csc-scc" is a placeholder.
    result = get_case_metadata("csc-scc", case_id)
    if "error" in result or not result.get("title"):
        return {"raw_input": citation}

    neutral, reporter = _split_canlii_citation(result.get("citation", ""))
    if neutral.lower().split()[1:2] == ["canlii"] and reporter:
        neutral, reporter = "", reporter

    date = result.get("decisionDate", "") or ""
    return {
        "style_of_cause": result.get("title", ""),
        "neutral_citation": neutral,
        "reporter": reporter,
        "year": date[:4],
        "date": date,
        "url": result.get("longUrl", "") or result.get("url", ""),
    }


def get_legislation_databases(language: str = "en") -> dict:
    """获取 CanLII 法规数据库列表。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        t0 = time.time()
        with prof.measure("http.canlii_legislation_databases", endpoint="/legislationBrowse/{language}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/legislationBrowse/{language}/",
                params={"api_key": key},
                read_timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"get_legislation_databases({language})", time.time() - t0)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII get_legislation_databases failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}


# ── Process-lifetime cache for legislation listings ──────────────────────────
# A listing is a multi-second, multi-megabyte request (Ontario's regulations are
# ~4 100 entries / 1.3 MB) and changes only when CanLII publishes, so re-fetching
# it per query both adds latency and burns quota — enough of it that consecutive
# queries could be throttled into a spurious "couldn't verify" answer.
# Successful responses only: an error must stay retryable.
_LEGISLATION_CACHE: dict[tuple[str, str], dict] = {}


def clear_legislation_cache() -> None:
    """Drop the cached legislation listings (tests; not used in production)."""
    _LEGISLATION_CACHE.clear()


def browse_legislation_in_database(
    database_id: str,
    language: str = "en",
) -> dict:
    """浏览指定法规数据库的全部法规列表。

    Args:
        database_id: CanLII 法规数据库 ID（如 "ons"、"cas"）。
        language: "en" 或 "fr"。

    返回原始 JSON，包含 "legislations" 列表。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    if not database_id:
        return {"error": "database_id required"}
    if not database_id.strip():
        return {"error": "database_id required"}

    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    cache_key = (database_id, language)
    cached = _LEGISLATION_CACHE.get(cache_key)
    if cached is not None:
        logger.debug("[DUR] CanLII browse_legislation_in_database(%s) — cache hit", database_id)
        return cached

    try:
        _is_first = _mark_first_canlii()
        if _is_first:
            logger.debug("[DUR] CanLII browse_legislation_in_database — FIRST call (DNS + TCP setup expected)")
        _http_t0 = time.perf_counter()
        with prof.measure("http.canlii_legislation_browse", endpoint="/legislationBrowse/{language}/{database_id}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/legislationBrowse/{language}/{database_id}/",
                params={"api_key": key},
                read_timeout=30,
            )
        _http_elapsed = time.perf_counter() - _http_t0
        logger.debug("[DUR] CanLII browse_legislation_in_database(%s) — %.1fms  first=%s", database_id, _http_elapsed * 1000, _is_first)
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"browse_legislation_in_database({database_id})",
                _http_elapsed,
            )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "error" not in payload:
            _LEGISLATION_CACHE[cache_key] = payload
        return payload
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII browse_legislation_in_database failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}
