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
        results = data.get("results", [])
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
                    results = response.json().get("results", [])
                except requests.exceptions.RequestException:
                    results = []
        if not results:
            return {"raw_input": citation}
        return _map_fields(results[0])
    except requests.exceptions.RequestException as e:
        return {"raw_input": citation, "error": f"A2AJ fetch failed: {e}"}


def _map_fields(result: dict) -> dict:
    """将 A2AJ 返回字段映射到 detect_type 能识别的格式。"""
    citation = result.get("citation_en", "")
    name = result.get("name_en", "")
    date = result.get("document_date_en", "")
    year = date[:4] if date else ""

    # 判断是判例还是法规
    dataset = result.get("dataset", "")
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
        reporter = result.get("citation2_en", "")
        return {
            "style_of_cause": name,
            "neutral_citation": citation,
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
        return resp.json().get("results", [])
    except requests.exceptions.RequestException:
        return []