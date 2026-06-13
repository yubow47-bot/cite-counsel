"""LEGISinfo API: fetch current session bills, find by number, build McGill citation (no LLM).

Endpoint: https://www.parl.ca/legisinfo/en/bills/json  (~200KB, ~176 bills, no key)
"""

import re
import json
import os
import time
import requests

CACHE_TTL = 3600  # 1 hour — bills change throughout session

BILLS_URL = "https://www.parl.ca/legisinfo/en/bills/json"
_CACHE = None
_CACHE_TIME = 0


def _fetch_json() -> list | None:
    """Download the LEGISinfo bill list. Returns list of bill dicts, or None."""
    try:
        resp = requests.get(BILLS_URL, timeout=30, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return None


def fetch_legisinfo_bills(force_refresh: bool = False) -> list:
    """Get bill list with cache. Cache TTL = CACHE_TTL seconds."""
    global _CACHE, _CACHE_TIME
    now = time.time()
    if not force_refresh and _CACHE is not None and (now - _CACHE_TIME) < CACHE_TTL:
        return _CACHE
    data = _fetch_json()
    if data is not None:
        _CACHE = data
        _CACHE_TIME = now
    return _CACHE or []


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


def find_bill(bill_number: str, session: str | None = None) -> dict | None:
    """Find a bill in the current session list.

    Args:
        bill_number: e.g. "C-22", "Bill C-22", "S-2"
        session: optional ParlSessionCode to filter (e.g. "45-1")

    Returns the bill record dict, or None.
    """
    target = _normalize_bill_number(bill_number)
    bills = fetch_legisinfo_bills()
    for b in bills:
        if _normalize_bill_number(b.get("BillNumberFormatted", "")) == target:
            if session is None or b.get("ParlSessionCode") == session:
                return b
    return None


def build_bill_citation(record: dict, pinpoint: str | None = None) -> str:
    """Assemble a McGill-format bill citation from LEGISinfo data (no LLM).

    Format: Bill {number}, {long_title}, {session_ordinal} Sess, {parl_ordinal} Parl, {year}{, cl {pinpoint}}.

    Example: Bill C-22, An Act respecting lawful access, 1st Sess, 45th Parl, 2026.
    """
    number = record.get("BillNumberFormatted", "?")
    title = record.get("LongTitleEn", "").strip() or record.get("ShortTitleEn", "").strip() or "?"
    parl = record.get("ParliamentNumber", 0) or 0
    sess = record.get("SessionNumber", 0) or 0
    year = ""
    for date_field in ["PassedHouseFirstReadingDateTime", "PassedSenateFirstReadingDateTime",
                        "LatestActivityDateTime", "IntroducedDateTime"]:
        val = record.get(date_field) or ""
        if val:
            m = re.search(r"\b(19\d{2}|20\d{2})\b", val)
            if m:
                year = m.group(1)
                break

    citation = f"Bill {number}, {title}, {_ordinal(sess)} Sess, {_ordinal(parl)} Parl"
    if year:
        citation += f", {year}"
    if pinpoint:
        citation += f", cl {pinpoint}"
    citation += "."

    return citation


def build_bill_scaffold(bill_number: str) -> str:
    """Return a scaffold McGill citation for a bill number not found in current session.
    User can fill in the blanks.
    """
    num = _normalize_bill_number(bill_number)
    return f"Bill {num}, [Full Title], [Session Ordinal] Sess, [Parliament Ordinal] Parl, [Year]."
