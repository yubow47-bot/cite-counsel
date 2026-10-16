import re
import time
import logging

import requests
from local_tools.utils import a2aj_session, request_with_retry
from local_tools.source_status import SourceRequestError, SourceResults, failure_reason

from local_tools import timing_util as timing
from profiling import timing as prof

logger = logging.getLogger(__name__)


# Case citation shapes, for pulling the citation out of a "name + citation" query:
#   "[1959] SCR 121" · "[1999] 1 SCR 688" · "[1930] AC 124"   bracketed year
#   "(1993), 83 CCC (3d) 346"                                  parenthesised year
#   "2012 SCC 13" · "2012 CanLII 27167"                        neutral
_CASE_CITATION_RE = re.compile(
    r"\[(?:1[89]|20)\d{2}\]\s*(?:\d+\s+)?[A-Za-z][A-Za-z.]*(?:\s+[A-Za-z][A-Za-z.]*)*\s+\d+"
    r"|\((?:1[89]|20)\d{2}\)\s*,?\s*(?:\d+\s+)?[A-Za-z][A-Za-z.]*"
    r"(?:\s*\(\d+[a-z]{0,2}\))?\s+\d+"
    r"|\b(?:1[89]|20)\d{2}\s+[A-Za-z]{2,10}\s+\d+\b"
)


def _extract_case_citation(text: str) -> str:
    """Pull a case citation out of a free-text query, or "" if there is none.

    "Roncarelli v Duplessis [1959] SCR 121" → "[1959] SCR 121"
    "R v Gladue [1999] 1 SCR 688"           → "[1999] 1 SCR 688"
    "2012 SCC 13"                           → "2012 SCC 13"
    "Roncarelli v Duplessis"                → ""
    """
    if not text:
        return ""
    m = _CASE_CITATION_RE.search(text)
    return m.group(0).strip() if m else ""


A2AJ_BASE = "https://api.a2aj.ca"

# ── Track first HTTP call to A2AJ ──
_first_a2aj_call = True


def _mark_first_a2aj() -> bool:
    global _first_a2aj_call
    if _first_a2aj_call:
        _first_a2aj_call = False
        return True
    return False


def _request_results(endpoint: str, params: dict) -> SourceResults:
    """Validate the service envelope; malformed payloads are never no-match."""
    operation = f"{endpoint} {params.get('doc_type', '')}".strip()
    try:
        response = request_with_retry(a2aj_session, "GET", f"{A2AJ_BASE}{endpoint}",
                                      params=params, read_timeout=15)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("Invalid result envelope")
    except (requests.exceptions.RequestException, ValueError, TypeError) as exc:
        raise SourceRequestError("a2aj", operation, reason=failure_reason(exc),
                                 coverage={"status": "failed", "source": "a2aj", "endpoint": endpoint,
                                           "query": params.get("query") or params.get("citation"),
                                           "doc_type": params.get("doc_type")}) from exc
    records = [r for r in data["results"] if isinstance(r, dict)]
    rejected = len(data["results"]) - len(records)
    if data["results"] and not records:
        raise SourceRequestError("a2aj", operation, reason="invalid_records")
    return SourceResults(records, coverage={"status": "partial" if rejected else "complete",
                                           "source": "a2aj", "endpoint": endpoint,
                                           "doc_type": params.get("doc_type"),
                                           "query": params.get("query") or params.get("citation"),
                                           "returned": len(records), "rejected_records": rejected})


def fetch_by_citation(citation: str, doc_type: str = "cases") -> dict:
    """Resolve a citation; errors remain structured for legacy dict callers."""
    try:
        results = _request_results("/fetch", {"citation": citation, "doc_type": doc_type})
        searched = [doc_type]
        if not results and doc_type == "cases" and " v. " not in citation and " v " not in citation:
            try:
                results = _request_results("/fetch", {"citation": citation, "doc_type": "laws"})
            except SourceRequestError as exc:
                exc.coverage["completed_doc_types"] = searched
                raise
            searched.append("laws")
        if not results:
            return {"raw_input": citation, "_source_coverage": {**results.coverage, "searched_doc_types": searched}}
        mapped = _map_fields(results[0])
        mapped["_source_coverage"] = {**results.coverage, "searched_doc_types": searched}
        return mapped
    except SourceRequestError as exc:
        logger.warning("A2AJ lookup failed: %s", exc.reason)
        return {"raw_input": citation, **exc.content()}



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
    """将 A2AJ 返回字段映射到引文字段格式。"""
    citation = result.get("citation_en", "")
    name = result.get("name_en", "")
    date = result.get("document_date_en", "")
    year = date[:4] if date else ""

    # 判断是判例还是法规（dataset may be present-but-null in degraded payloads）
    dataset = result.get("dataset") or ""
    dataset_kind = re.split(r"[-_]", dataset.upper(), maxsplit=1)[0]
    is_legislation = dataset_kind in {"LEGISLATION", "REGULATIONS"}

    if is_legislation:
        return {
            "statute_title": name,
            "citation": citation,
            "jurisdiction": _extract_jurisdiction(dataset),
            "year": year,
            "url": result.get("url_en") or result.get("source_url_en", ""),
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


# A2AJ legislation datasets are "<KIND>-<JURISDICTION>": "LEGISLATION-ON",
# "REGULATIONS-QC", "LEGISLATION-FED".
_JURISDICTION_NAMES = {
    "FED": "Canada",
    "CA": "Canada",
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
    "YK": "Yukon",
}


def _extract_jurisdiction(dataset: str) -> str:
    """从 dataset 字段提取管辖区（"LEGISLATION-AB" → "Alberta"）。

    Reads the code after the final hyphen.  The previous implementation tested
    ``"ON" in dataset`` — and "LEGISLATION" itself contains "ON", so EVERY
    non-federal statute came back as Ontario (an Alberta statute was cited as
    Ontario legislation).
    """
    if not dataset:
        return ""
    code = re.split(r"[-_]", dataset.upper())[-1].strip()
    return _JURISDICTION_NAMES.get(code, "")


def _extract_year(text: str) -> tuple[str, str | None]:
    """从文本中提取4位年份，返回(清洗后的文本, 年份或None)。"""
    m = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if m:
        year = m.group(1)
        cleaned = re.sub(r'\b' + year + r'\b', '', text).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned, year
    return text, None


def search_laws_by_name(name: str, size: int = 6) -> list:
    """Search statutes and regulations; unavailable services raise a retryable error."""
    if not name or not name.strip():
        return SourceResults()
    with prof.measure("http.a2aj_search_laws", endpoint="/search", doc_type="laws"):
        return _request_results("/search", {"query": name.strip(), "doc_type": "laws",
                                           "size": size, "search_type": "name"})


def search_cases_multi(query: str, size: int = 45, search_type: str = "name",
                       start_date: str | None = None, end_date: str | None = None) -> list:
    """Search cases by name, resolving an embedded citation first."""
    embedded = _extract_case_citation(query)
    if embedded and embedded != query.strip():
        direct = fetch_by_citation(embedded)
        if direct.get("error"):
            raise SourceRequestError("a2aj", "citation lookup", reason=direct.get("reason", "request_failed"),
                                     coverage=direct.get("coverage"))
        if direct.get("style_of_cause"):
            return SourceResults([direct], coverage=direct.get("_source_coverage"))
    clean_query, year = _extract_year(query)
    if year and not start_date and not end_date:
        start_date, end_date = f"{year}-01-01", f"{year}-12-31"
    params = {"query": clean_query, "doc_type": "cases", "size": size, "search_type": search_type}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    with prof.measure("http.a2aj_search_query", endpoint="/search"):
        results = _request_results("/search", params)
    results.coverage.update(start_date=start_date, end_date=end_date)
    return results
