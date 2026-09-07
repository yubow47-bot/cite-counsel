"""LEGISinfo API: fetch bills by session, find by number, build McGill citation (no LLM).

Endpoint: https://www.parl.ca/legisinfo/en/bills/json  (?parlsession=XX-X for historical)
"""

import re
import json
import os
import time
import logging
import requests
from datetime import datetime

from local_tools.format_util import _wrap_italic
from local_tools.utils import legisinfo_session, request_with_retry

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


def _session_end_year(session_code: str) -> int | None:
    """Return the end year for a session code, or None if current/unknown."""
    info = SESSION_MAP.get(session_code)
    if not info:
        return None
    return info[1]


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
    """Download the LEGISinfo bill list. Returns list of bill dicts, or None on failure."""
    try:
        url = BILLS_URL
        if session:
            url = f"{BILLS_URL}?parlsession={session}"
        resp = request_with_retry(legisinfo_session, "GET", url, read_timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return _restore_legacy_fields(data)
    except Exception:
        pass
    return None


def fetch_legisinfo_bills(force_refresh: bool = False, session: str | None = None) -> list:
    """Get bill list with per-session cache. Cache TTL = CACHE_TTL seconds.

    Args:
        force_refresh: bypass cache.
        session: ParlSessionCode. None = current session.

    Returns list of bill dicts (may be empty on error).
    """
    global _CACHE, _CACHE_TIME
    key = session or "_default"
    now = time.time()

    # Check cache hit
    if not force_refresh and key in _CACHE and _CACHE[key] is not None and (now - _CACHE_TIME.get(key, 0)) < CACHE_TTL:
        _age = now - _CACHE_TIME.get(key, now)
        logger.debug("[DUR] LEGISinfo fetch_legisinfo_bills — CACHE HIT (key=%s, age=%.0fs)", key, _age)
        return _CACHE[key]

    _is_first = _mark_first_legisinfo_fetch()
    _fetch_t0 = time.perf_counter()
    if _is_first:
        logger.debug("[DUR] LEGISinfo fetch_legisinfo_bills — FIRST fetch (cold cache, HTTP + JSON parse)")

    data = _fetch_json(session=session)
    _fetch_elapsed = time.perf_counter() - _fetch_t0

    if data is not None:
        _CACHE[key] = data
        _CACHE_TIME[key] = now
        logger.debug("[DUR] LEGISinfo fetch_legisinfo_bills — CACHE MISS, fetched %d bills in %.1fms  first=%s", len(data), _fetch_elapsed * 1000, _is_first)
    else:
        logger.debug("[DUR] LEGISinfo fetch_legisinfo_bills — FETCH FAILED in %.1fms  first=%s", _fetch_elapsed * 1000, _is_first)

    return _CACHE.get(key) or []


def _normalize_bill_number(raw: str) -> str:
    """Normalize bill number: strip 'Bill' prefix, normalize case/whitespace."""
    s = raw.strip()
    s = re.sub(r"(?i)^bill\s+", "", s)
    s = s.upper().replace(" ", "-").replace("--", "-").strip("-")
    return s


def _ordinal(n: int) -> str:
    """Return ordinal string: 1 → 1st, 2 → 2nd, 3 → 3rd, 21 → 21st, etc."""
    suffix = "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def find_bill(bill_number: str, year: int | None = None) -> dict | None:
    """Find a bill in LEGISinfo, optionally by year for historical session lookup.

    Returns the FIRST bill record dict matching the number, or None.
    When multiple sessions match the same year, this returns the first hit
    across sessions. Use find_bills() for full multi-session disambiguation.

    Args:
        bill_number: e.g. "C-22", "Bill C-22", "S-2"
        year: 4-digit year. If given, resolves to candidate session(s)
              via SESSION_MAP and searches only those sessions.
              Never falls back to current session.

    Returns the bill record dict, or None if not found.
    """
    target = _normalize_bill_number(bill_number)

    if year is not None:
        sessions = _year_to_sessions(year)
        for sess in sessions:
            bills = fetch_legisinfo_bills(session=sess)
            for b in bills:
                if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
                    return b
        return None

    bills = fetch_legisinfo_bills(session=_get_current_session())
    for b in bills:
        if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
            return b
    return None


def find_bills(bill_number: str, year: int | None = None) -> list[dict]:
    """Find ALL bills matching the number across year-matched sessions.

    Per-session fault tolerance: each session fetch is wrapped independently.
    A failing session is logged and skipped; results from successful sessions
    are preserved. Not-found is returned ONLY when every candidate session fails.

    Args:
        bill_number: e.g. "C-22", "S-2"
        year: optional 4-digit year. None = current session only.

    Returns list of bill record dicts (may be empty).
    """
    target = _normalize_bill_number(bill_number)
    results: list[dict] = []
    seen_bill_ids: set[int] = set()
    failed_sessions: list[str] = []

    if year is not None:
        sessions = _year_to_sessions(year)
    else:
        sessions = [_get_current_session()]

    if not sessions:
        return []

    for sess in sessions:
        # Cached fetch (same path as find_bill / boot warm-up) — a historical
        # query no longer re-downloads the full list for every candidate
        # session.  Empty result covers both fetch failure and empty session.
        bills = fetch_legisinfo_bills(session=sess)
        if not bills:
            failed_sessions.append(sess)
            continue
        for b in bills:
            if not isinstance(b, dict):
                continue
            if _normalize_bill_number(b.get("BillNumberFormatted", "")) != target:
                continue
            # Dedupe on BillId when present; a record with no id is still a
            # valid match and must not be silently dropped.
            bid = b.get("BillId") or b.get("Id")
            if bid:
                if bid in seen_bill_ids:
                    continue
                seen_bill_ids.add(bid)
            results.append(b)

    if failed_sessions:
        logger.warning("[LEGISinfo] failed sessions for bill %s: %s", bill_number, failed_sessions)

    return results

def build_bill_citation(record: dict, pinpoint: str | None = None) -> str:
    """Assemble a McGill-format bill citation from LEGISinfo data (no LLM).

    Format: Bill {number}, *{title}*, {session_ordinal} Sess,
            {parl_ordinal} Parl, {year}{, cl {pinpoint}}.

    Title is wrapped in Markdown *italic* (frontend renderCitation converts *x* → <em>).
    Year is derived from structured date fields first, falling back to
    the session's end year from SESSION_MAP if no date field is available.
    """
    number = record.get("BillNumberFormatted", "?")

    title = record.get("LongTitleEn", "").strip() or record.get("ShortTitleEn", "").strip() or "?"
    if title != "?":
        title = _wrap_italic(title)

    parl = record.get("ParliamentNumber", 0) or 0
    sess = record.get("SessionNumber", 0) or 0

    year = ""
    for date_field in [
        "PassedHouseFirstReadingDateTime", "PassedSenateFirstReadingDateTime",
        "LatestActivityDateTime", "IntroducedDateTime",
    ]:
        val = record.get(date_field) or ""
        if val:
            m = re.search(r"\b(19\d{2}|20\d{2})\b", val)
            if m:
                year = m.group(1)
                break
    if not year:
        ps_code = record.get("ParlSessionCode", "")
        end_yr = _session_end_year(ps_code)
        if end_yr is not None:
            year = str(end_yr)
        elif ps_code:
            year = str(datetime.now().year)

    citation = f"Bill {number}, {title}, {_ordinal(sess)} Sess, {_ordinal(parl)} Parl"
    if year:
        citation += f", {year}"
    if pinpoint:
        citation += f", cl {pinpoint}"
    citation += "."

    return citation
