"""LEGISinfo API: fetch bills by session, find by number, build McGill citation (no LLM).

Endpoint: https://www.parl.ca/legisinfo/en/bills/json  (?parlsession=XX-X for historical)
"""

import re
import json
import os
import time
import requests
from datetime import datetime

CACHE_TTL = 3600  # 1 hour — bills change throughout session

BILLS_URL = "https://www.parl.ca/legisinfo/en/bills/json"

# Session-keyed cache: dict[session_code] -> list[dict] | None
_CACHE: dict[str, list | None] = {}
_CACHE_TIME: dict[str, float] = {}

# ── Parliamentary session date ranges (hardcoded, 1994–present) ──
# Each entry: (start_year, end_year) — used for year→session resolution.
# end_year is None for the current ongoing session.
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


def _year_to_sessions(year: int) -> list[str]:
    """Return session codes whose date range covers the given year.

    Matches if start_year <= year <= end_year.
    For the current session (end_year=None), matches if year >= start_year.
    Multiple sessions can match the same year (e.g. 1997 → 35-2, 36-1).
    """
    candidates = []
    for code, (start, end) in SESSION_MAP.items():
        if start is not None and start <= year:
            if end is None:
                # Current ongoing session — matches any year >= start
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


def _fetch_json(session: str | None = None) -> list | None:
    """Download the LEGISinfo bill list for an optional session.

    Args:
        session: ParlSessionCode e.g. "35-2". None = current session.

    Returns list of bill dicts, or None on failure.
    """
    try:
        url = BILLS_URL
        if session:
            url = f"{BILLS_URL}?parlsession={session}"
        resp = requests.get(url, timeout=30, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
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
    if not force_refresh and key in _CACHE and _CACHE[key] is not None and (now - _CACHE_TIME.get(key, 0)) < CACHE_TTL:
        return _CACHE[key]
    data = _fetch_json(session=session)
    if data is not None:
        _CACHE[key] = data
        _CACHE_TIME[key] = now
    return _CACHE.get(key) or []


def _normalize_bill_number(raw: str) -> str:
    """Normalize bill number: strip 'Bill' prefix, normalize case/whitespace.

    Bill C-22 → C-22, C-22 → C-22, bill s-2 → S-2
    """
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

    Args:
        bill_number: e.g. "C-22", "Bill C-22", "S-2"
        year: 4-digit year. If given, resolves to candidate session(s)
              via SESSION_MAP and searches only those sessions.
              Never falls back to current session.
              If None, searches only the current session (original behaviour).

    Returns the bill record dict, or None if not found.
    """
    target = _normalize_bill_number(bill_number)

    if year is not None:
        # Year-based lookup: resolve sessions, search only those
        sessions = _year_to_sessions(year)
        for sess in sessions:
            bills = fetch_legisinfo_bills(session=sess)
            for b in bills:
                if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
                    return b
        return None  # No fallback — never return wrong-session bill

    # No year: current session only (original behaviour)
    bills = fetch_legisinfo_bills()
    for b in bills:
        if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
            return b
    return None


def find_bills(bill_number: str, year: int | None = None) -> list[dict]:
    """Find ALL bills matching the number across sessions.

    When year is given, collects every match from every year-matched session.
    When no year given, searches only the current session (returns at most 1).
    Never falls back to current session for years outside coverage.

    Args:
        bill_number: e.g. "C-22", "S-2"
        year: optional 4-digit year for historical session resolution.

    Returns list of bill record dicts (may be empty).
    """
    target = _normalize_bill_number(bill_number)
    results: list[dict] = []
    seen_bill_ids: set[int] = set()

    if year is not None:
        sessions = _year_to_sessions(year)
    else:
        sessions = []

    if not sessions:
        # No year (or year before coverage) — only search if no year specified
        if year is None:
            bills = fetch_legisinfo_bills()
            for b in bills:
                if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
                    bid = b.get("BillId", 0)
                    if bid and bid not in seen_bill_ids:
                        seen_bill_ids.add(bid)
                        results.append(b)
        return results

    for sess in sessions:
        bills = fetch_legisinfo_bills(session=sess)
        for b in bills:
            if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
                bid = b.get("BillId", 0)
                if bid and bid not in seen_bill_ids:
                    seen_bill_ids.add(bid)
                    results.append(b)
    return results


def build_bill_citation(record: dict, pinpoint: str | None = None) -> str:
    """Assemble a McGill-format bill citation from LEGISinfo data (no LLM).

    Format: Bill {number}, *{long_title}*, {session_ordinal} Sess, {parl_ordinal} Parl, {year}{, cl {pinpoint}}.

    Title is wrapped in Markdown *italic* (frontend renders *x* → <em>).
    Year is derived from structured date fields first, falling back to
    the session's end year from SESSION_MAP if no date field is available.

    Example: Bill C-22, *An Act respecting lawful access*, 1st Sess, 45th Parl, 2026.
    """
    number = record.get("BillNumberFormatted", "?")

    # Title — italicised with Markdown asterisks
    title = record.get("LongTitleEn", "").strip() or record.get("ShortTitleEn", "").strip() or "?"
    if title != "?":
        title = f"*{title}*"

    parl = record.get("ParliamentNumber", 0) or 0
    sess = record.get("SessionNumber", 0) or 0

    # Year: prefer structured date fields, fall back to session end year
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
            # Current session with no end date yet — use current year
            year = str(datetime.now().year)

    citation = f"Bill {number}, {title}, {_ordinal(sess)} Sess, {_ordinal(parl)} Parl"
    if year:
        citation += f", {year}"
    if pinpoint:
        citation += f", cl {pinpoint}"
    citation += "."

    return citation


def build_bill_scaffold(bill_number: str) -> str:
    """Return a scaffold McGill citation for a bill number not found in any session.
    User can fill in the blanks.
    """
    num = _normalize_bill_number(bill_number)
    return f"Bill {num}, [Full Title], [Session Ordinal] Sess, [Parliament Ordinal] Parl, [Year]."
