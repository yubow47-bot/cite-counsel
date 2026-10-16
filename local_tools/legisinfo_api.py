"""LEGISinfo API: fetch bills by session, find by number, build McGill citation (no LLM).

Endpoint: https://www.parl.ca/legisinfo/en/bills/json  (?parlsession=XX-X for historical)
"""

import re
import time
import logging

from local_tools.utils import legisinfo_session, request_with_retry
from local_tools.source_status import SourceRequestError, SourceResults, failure_reason

logger = logging.getLogger(__name__)

CACHE_TTL = 3600  # 1 hour — bills change throughout session

BILLS_URL = "https://www.parl.ca/legisinfo/en/bills/json"

_CACHE: dict[str, list | None] = {}
_CACHE_TIME: dict[str, float] = {}

# ── Track first-ever LEGISinfo fetch (cache miss + HTTP) ──
_first_legisinfo_fetch = True


def _mark_first_legisinfo_fetch() -> bool:
    global _first_legisinfo_fetch
    if _first_legisinfo_fetch:
        _first_legisinfo_fetch = False
        return True
    return False

# ── Parliamentary session date ranges (hardcoded, 1994–present) ──
SESSION_MAP: dict[str, tuple[int | None, int | None]] = {
    "45-1": (2025, None),
    "44-1": (2021, 2025),
    "43-2": (2020, 2021),
    "43-1": (2019, 2020),
    "42-1": (2015, 2019),
    "41-2": (2013, 2015),
    "41-1": (2011, 2013),
    "40-3": (2010, 2011),
    "40-2": (2009, 2009),
    "40-1": (2008, 2008),
    "39-2": (2007, 2008),
    "39-1": (2006, 2007),
    "38-1": (2004, 2005),
    "37-3": (2004, 2004),
    "37-2": (2002, 2003),
    "37-1": (2001, 2002),
    "36-2": (1999, 2000),
    "36-1": (1997, 1999),
    "35-2": (1996, 1997),
    "35-1": (1994, 1996),
}


def _get_current_session() -> str:
    """Return the current parliamentary session code (the one with end_year=None)."""
    for code, (start, end) in SESSION_MAP.items():
        if end is None:
            return code
    return "45-1"


def _year_to_sessions(year: int) -> list[str]:
    """Return session codes whose date range covers the given year."""
    candidates = []
    for code, (start, end) in SESSION_MAP.items():
        if start is not None and start <= year:
            if end is None:
                if year >= start:
                    candidates.append(code)
            elif end is not None and year <= end:
                candidates.append(code)
    return candidates


def _restore_legacy_fields(records: list) -> list:
    """Map new-schema LEGISinfo records onto the legacy field names.

    LEGISinfo changed their JSON feed server-side: ``BillNumberFormatted``,
    ``BillId``, and ``ParlSessionCode`` disappeared from each record.  The
    replacements are ``NumberCode`` (full number, e.g. "C-32"), ``Id``, and
    ``ParliamentNumber``+``SessionNumber`` respectively.  Re-adding the
    legacy keys here keeps every downstream consumer (bill-number matching,
    dedup by BillId, citation assembly) working with either shape.
    """
    for b in records:
        if not isinstance(b, dict):
            continue
        if not b.get("BillNumberFormatted"):
            num = b.get("NumberCode")
            if num:
                b["BillNumberFormatted"] = num
        if not b.get("BillId"):
            bid = b.get("Id")
            if bid:
                b["BillId"] = bid
        if not b.get("ParlSessionCode"):
            parl, sess = b.get("ParliamentNumber"), b.get("SessionNumber")
            if parl and sess:
                b["ParlSessionCode"] = "%s-%s" % (parl, sess)
    return records


def _fetch_json(session: str | None = None) -> list | None:
    """Download the bill list; failed or malformed responses raise explicitly."""
    try:
        url = BILLS_URL
        if session:
            url = f"{BILLS_URL}?parlsession={session}"
        resp = request_with_retry(legisinfo_session, "GET", url, read_timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError("Invalid bill-list envelope")
        valid = [b for b in data if isinstance(b, dict)]
        if data and not valid:
            raise ValueError("No valid bill records")
        return SourceResults(_restore_legacy_fields(valid),
                             coverage={"status": "partial" if len(valid) < len(data) else "complete",
                                       "rejected_records": len(data) - len(valid)})
    except Exception as exc:
        raise SourceRequestError("legisinfo", "bill list", reason=failure_reason(exc),
                                 coverage={"status": "failed", "source": "legisinfo",
                                           "requested_sessions": [session or "current"],
                                           "failed_sessions": [session or "current"]}) from exc


def fetch_legisinfo_bills(force_refresh: bool = False, session: str | None = None) -> list:
    """Get bill list with per-session cache. Cache TTL = CACHE_TTL seconds.

    Args:
        force_refresh: bypass cache.
        session: ParlSessionCode. None = current session.

    Returns a list-compatible SourceResults; cache age and stale fallback are explicit.
    """
    global _CACHE, _CACHE_TIME
    key = session or "_default"
    now = time.time()

    # Check cache hit
    if not force_refresh and key in _CACHE and _CACHE[key] is not None and (now - _CACHE_TIME.get(key, 0)) < CACHE_TTL:
        _age = now - _CACHE_TIME.get(key, now)
        logger.debug("[DUR] LEGISinfo fetch_legisinfo_bills — CACHE HIT (key=%s, age=%.0fs)", key, _age)
        return SourceResults(_CACHE[key], coverage={**getattr(_CACHE[key], "coverage", {"status": "complete"}),
                             "source": "legisinfo", "session": session or "current", "cache": "hit",
                             "cache_age_seconds": max(0, _age), "stale": False,
                             "retrieved_at": _CACHE_TIME[key]})

    _is_first = _mark_first_legisinfo_fetch()
    _fetch_t0 = time.perf_counter()
    if _is_first:
        logger.debug("[DUR] LEGISinfo fetch_legisinfo_bills — FIRST fetch (cold cache, HTTP + JSON parse)")

    try:
        data = _fetch_json(session=session)
        if data is None:
            raise SourceRequestError("legisinfo", "bill list", reason="empty_response")
    except SourceRequestError as exc:
        if key not in _CACHE or _CACHE[key] is None:
            raise
        age = max(0, now - _CACHE_TIME.get(key, 0))
        return SourceResults(_CACHE[key], coverage={"status": "partial", "source": "legisinfo",
                             "session": session or "current", "cache": "fallback", "stale": True,
                             "cache_age_seconds": age, "retrieved_at": _CACHE_TIME.get(key),
                             "refresh_error": exc.reason, "failed_sessions": [session or "current"]})
    _fetch_elapsed = time.perf_counter() - _fetch_t0

    _CACHE[key] = data
    _CACHE_TIME[key] = now
    logger.debug("[DUR] LEGISinfo fetched %d bills in %.1fms first=%s", len(data), _fetch_elapsed * 1000, _is_first)
    return SourceResults(data, coverage={**getattr(data, "coverage", {"status": "complete"}),
                         "source": "legisinfo", "session": session or "current", "cache": "miss",
                         "cache_age_seconds": 0, "stale": False, "retrieved_at": now})


def _normalize_bill_number(raw: str) -> str:
    """Normalize bill number: strip 'Bill' prefix, normalize case/whitespace."""
    s = raw.strip()
    s = re.sub(r"(?i)^bill\s+", "", s)
    s = s.upper().replace(" ", "-").replace("--", "-").strip("-")
    return s


def ordinal_suffix(n: int) -> str:
    """Return ordinal string: 1 → 1st, 2 → 2nd, 3 → 3rd, 21 → 21st, etc."""
    suffix = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def find_bill(bill_number: str, year: int | None = None) -> dict | None:
    """Legacy first-candidate helper; unavailable searches still raise."""
    results = find_bills(bill_number, year)
    return results[0] if results else None


def find_bills(bill_number: str, year: int | None = None) -> list[dict]:
    """Return matches and per-session coverage, preserving partial results."""
    target = _normalize_bill_number(bill_number)
    sessions = _year_to_sessions(year) if year is not None else [_get_current_session()]
    results, seen_bill_ids, failures, completed, session_coverage = [], set(), [], [], []
    if not sessions:
        return SourceResults(coverage={"status": "partial", "source": "legisinfo",
                                      "requested_year": year, "requested_sessions": [],
                                      "completed_sessions": [], "failed_sessions": [],
                                      "reason": "year_outside_configured_session_coverage"})
    for sess in sessions:
        try:
            bills = fetch_legisinfo_bills(session=sess)
            if bills is None:
                raise SourceRequestError("legisinfo", "bill list", reason="empty_response")
        except SourceRequestError as exc:
            failures.append(sess)
            session_coverage.append({"session": sess, "status": "failed", "reason": exc.reason})
            continue
        coverage = getattr(bills, "coverage", {"status": "complete"})
        session_coverage.append({"session": sess, **coverage})
        completed.append(sess)
        if coverage.get("status") != "complete":
            failures.append(sess)
        for bill in bills:
            if not isinstance(bill, dict):
                continue
            if _normalize_bill_number(bill.get("BillNumberFormatted", "")) != target:
                continue
            bill_id = bill.get("BillId") or bill.get("Id")
            if bill_id:
                if bill_id in seen_bill_ids:
                    continue
                seen_bill_ids.add(bill_id)
            results.append(bill)
    coverage = {"status": "partial" if failures else "complete", "source": "legisinfo",
                "bill_number": target, "requested_year": year, "requested_sessions": sessions,
                "completed_sessions": completed, "failed_sessions": failures,
                "sessions": session_coverage}
    if not completed:
        coverage["status"] = "failed"
        raise SourceRequestError("legisinfo", "bill search", reason="all_sessions_failed", coverage=coverage)
    return SourceResults(results, coverage=coverage)
