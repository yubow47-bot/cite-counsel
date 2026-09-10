import re
import time
import logging

import requests
from local_tools.utils import a2aj_session, request_with_retry

from local_tools import timing_util as timing
from profiling import timing as prof

logger = logging.getLogger(__name__)

A2AJ_BASE = "https://api.a2aj.ca"

# ── Track first HTTP call to A2AJ ──
_first_a2aj_call = True


def _mark_first_a2aj() -> bool:
    global _first_a2aj_call
    if _first_a2aj_call:
        _first_a2aj_call = False
        return True
    return False


def fetch_by_citation(citation: str, doc_type: str = "cases") -> dict:
    """按 citation 直接查询，返回结构化字段。"""
    try:
        _is_first = _mark_first_a2aj()
        if _is_first:
            logger.debug("[DUR] A2AJ fetch_by_citation — FIRST call (DNS + TCP setup expected)")
        _http_t0 = time.perf_counter()
        with prof.measure("http.a2aj_fetch", endpoint="/fetch", doc_type=doc_type):
            response = request_with_retry(
                a2aj_session, "GET",
                f"{A2AJ_BASE}/fetch",
                params={"citation": citation, "doc_type": doc_type},
                read_timeout=15,
            )
        _http_elapsed = time.perf_counter() - _http_t0
        logger.debug("[DUR] A2AJ fetch_by_citation — %.1fms  first=%s", _http_elapsed * 1000, _is_first)
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"fetch({doc_type}) {citation[:40]}", _http_elapsed)
        response.raise_for_status()
        data = response.json()
        # Guard: A2AJ must return a JSON object; a bare array/string would crash .get()
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results and doc_type == "cases":
            # 查询串含 " v. " 或 " v " 的是案件引用，跳过 legislation fallback
            if " v. " in citation or " v " in citation:
                results = []
            else:
                try:
                    t0 = time.time()
                    with prof.measure("http.a2aj_fetch_fallback", endpoint="/fetch", doc_type="legislation"):
                        response = request_with_retry(
                            a2aj_session, "GET",
                            f"{A2AJ_BASE}/fetch",
                            params={"citation": citation, "doc_type": "legislation"},
                            read_timeout=15,
                        )
                    if timing.ENABLE_TIMING:
                        timing.report().add_a2aj(f"fetch(legislation fallback) {citation[:40]}", time.time() - t0)
                    response.raise_for_status()
                    fallback_data = response.json()
                    results = (
                        fallback_data.get("results", [])
                        if isinstance(fallback_data, dict)
                        else []
                    )
                except requests.exceptions.RequestException:
                    results = []
        if not results:
            return {"raw_input": citation}
        # Guard: an element of a misbehaving payload may be a string/null —
        # only dict records are mappable.
        first = results[0] if isinstance(results[0], dict) else None
        if first is None:
            return {"raw_input": citation}
        return _map_fields(first)
    except requests.exceptions.RequestException as e:
        logger.warning("A2AJ fetch failed: %s", e)
        return {"raw_input": citation, "error": "A2AJ database lookup failed. Try again or enter the citation manually."}


def _same_citation(a: str, b: str) -> bool:
    """归一化后比较两条引文是否同值（去点号、折叠空白、忽略大小写）。"""
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace(".", "")).strip().casefold()
    return _norm(a) == _norm(b)


# Neutral citation shape: "YYYY COURT N" — a bare year, a court/publisher code,
# and a sequence number ("2012 SCC 13", "2022 ONCA 39", "2012 CanLII 27167").
# A print/reporter citation instead leads with a bracketed or parenthesised year
# ("[1999] 1 SCR 688", "(1883) 8 App Cas 354"), which this deliberately rejects.
_NEUTRAL_CITATION_RE = re.compile(r"^\d{4}\s+[A-Za-z][A-Za-z]{1,9}\s+\d+$")


def _is_neutral_citation(citation: str) -> bool:
    """True when ``citation`` has the neutral-citation shape (no brackets)."""
    return bool(_NEUTRAL_CITATION_RE.match(citation.strip()))


def _map_fields(result: dict) -> dict:
    """将 A2AJ 返回字段映射到 detect_type 能识别的格式。"""
    citation = result.get("citation_en", "")
    name = result.get("name_en", "")
    date = result.get("document_date_en", "")
    year = date[:4] if date else ""

    # 判断是判例还是法规（dataset may be present-but-null in degraded payloads）
    dataset = result.get("dataset") or ""
    is_legislation = "LEGISLATION" in dataset.upper()

    if is_legislation:
        return {
            "statute_title": name,
            "neutral_citation": citation,
            "jurisdiction": _extract_jurisdiction(dataset),
            "year": year,
            "url": result.get("url_en", ""),
        }
    else:
        # ── Shape-based slotting, not positional ──────────────────────────
        # A2AJ puts the primary citation in citation_en and the parallel one in
        # citation2_en, but for pre-neutral-era cases (R v Gladue, Roncarelli)
        # citation_en holds a PRINT citation and citation2_en repeats it.
        # Slotting by position put a reporter citation into neutral_citation,
        # which routed the case to the juris.neutral subpattern — whose prompt
        # carries a neutral example ("R v King, 2002 SCC 10").  The model then
        # manufactured a neutral citation to fit the shape it was shown
        # ("[1999] 1 SCR 688" → "R v Gladue, 1999 SCC 688, [1999] 1 SCR 688";
        # "[1959] SCR 121" → the example citation verbatim).  Slotting by shape
        # leaves neutral_citation empty for these, so select_subpattern routes
        # to juris.reported_only and no neutral citation can be invented.
        neutral = ""
        reporter = ""
        for candidate in (citation, result.get("citation2_en", "") or ""):
            candidate = candidate.strip()
            if not candidate:
                continue
            if _is_neutral_citation(candidate):
                if not neutral:
                    neutral = candidate
            elif not reporter:
                reporter = candidate
        # Pre-neutral duplicates ("[1999] 1 SCR 688" twice, or with a dotted
        # "[1999] 1 S.C.R. 688" variant) collapse in the loop above — both are
        # print-shaped, so the second never reaches the free reporter slot.
        # This guard covers the remaining case: a dotted NEUTRAL variant
        # ("2012 SCC. 13") fails the shape test, lands in reporter, and would
        # otherwise render as "X, X."
        if neutral and reporter and _same_citation(neutral, reporter):
            reporter = ""
        return {
            "style_of_cause": name,
            "neutral_citation": neutral,
            "reporter": reporter,
            "year": year,
            "date": date,
            "url": result.get("url_en", ""),
        }


def _extract_jurisdiction(dataset: str) -> str:
    """从 dataset 字段提取管辖区。"""
    mapping = {
        "FED": "Canada",
        "ON": "Ontario",
        "BC": "British Columbia",
    }
    for key, value in mapping.items():
        if key in dataset.upper():
            return value
    return ""


def _extract_year(text: str) -> tuple[str, str | None]:
    """从文本中提取4位年份，返回(清洗后的文本, 年份或None)。"""
    m = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if m:
        year = m.group(1)
        cleaned = re.sub(r'\b' + year + r'\b', '', text).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned, year
    return text, None


def search_cases_multi(query: str, size: int = 45,
                       search_type: str = "name",
                       start_date: str | None = None,
                       end_date: str | None = None) -> list:
    """按名称搜索案件（A2AJ /search），返回结果列表。"""
    _is_first = _mark_first_a2aj()
    if _is_first:
        logger.debug("[DUR] A2AJ search_cases_multi — FIRST call (DNS + TCP setup expected)")
    _http_t0 = time.perf_counter()
    # 从 query 中提取年份并清洗
    clean_query, year = _extract_year(query)
    if year and not start_date and not end_date:
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

    params = {
        "query": clean_query,
        "doc_type": "cases",
        "size": size,
        "search_type": search_type,
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    try:
        with prof.measure("http.a2aj_search_query", endpoint="/search"):
            resp = request_with_retry(
                a2aj_session, "GET",
                f"{A2AJ_BASE}/search",
                params=params,
                read_timeout=15,
            )
        _http_elapsed = time.perf_counter() - _http_t0
        logger.debug("[DUR] A2AJ search_cases_multi — %.1fms  first=%s", _http_elapsed * 1000, _is_first)
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"search_cases_multi /search ({clean_query[:30]})", _http_elapsed)
        resp.raise_for_status()
        data = resp.json()
        # Guard: envelope must be a JSON object of dict records (same contract
        # as fetch_by_citation — an array/scalar envelope or junk elements
        # degrade to an empty result instead of raising).
        results = data.get("results", []) if isinstance(data, dict) else []
        return [r for r in results if isinstance(r, dict)]
    except requests.exceptions.RequestException:
        return []