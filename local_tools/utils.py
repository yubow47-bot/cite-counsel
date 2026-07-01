"""Shared utility functions for citation processing."""

import re
import requests as _requests

# ── Shared HTTP session for connection pooling ──────────────────────────────
#
# requests.Session() is NOT strictly thread-safe for concurrent *mutation* of
# session state (``cookies``, ``headers``, ``params`` dicts).  Connection
# pooling itself (the ``urllib3`` connection-pool machinery behind the
# ``.get()`` / ``.post()`` methods) IS safe for concurrent use.
#
# Rules:
#   * Do NOT set per-request headers or auth on the session object itself.
#     Pass them as call-level kwargs (``headers={...}``, ``auth=...``) instead.
#   * Do NOT read / write ``session.cookies``, ``session.headers``, or
#     ``session.params`` outside of a single-threaded init or shutdown path.
#
# One session per external provider so cookie jars and connection pools are
# never shared across different hosts / auth domains.
legisinfo_session = _requests.Session()
a2aj_session = _requests.Session()
canlii_session = _requests.Session()
crossref_session = _requests.Session()
openlibrary_session = _requests.Session()
deepseek_session = _requests.Session()
gemini_session = _requests.Session()
kimi_session = _requests.Session()
discord_session = _requests.Session()
generic_session = _requests.Session()

# Matches a base legal citation like "RSC 1985, c C-46", "SC 2002, c 1", "SOR/2000-111"
_CITATION_REGEX = re.compile(
    r"(?:RSC|SC|SOR|RRO|O\sReg|BC\sReg|RLRQ)\s[^,]+(?:,\s*c\s[^,]+)?",
)


def extract_case_pinpoint(raw_input: str) -> str:
    """Extract a pinpoint from a case-name (case_route) query string.

    Recognizes trailing patterns at the END of the input:
      "at para N"       e.g. "at para 2"
      "at paras N-M"    e.g. "at paras 10-15"
      "at N"            e.g. "at 47"
      "at p N"          e.g. "at p 5"
      "at pp N-M"       e.g. "at pp 10-15"

    Returns the matched string, or ``""`` if none found.
    """
    if not raw_input:
        return ""
    s = raw_input.strip()
    # Order matters: longer patterns first to avoid partial matches
    patterns = [
        r'at\s+paras\s+\d+(?:-\d+)$',
        r'at\s+para\s+\d+(?:-\d+)?$',
        r'at\s+pp\s+\d+(?:-\d+)$',
        r'at\s+p\s+\d+(?:-\d+)?$',
        r'at\s+\d+$',
    ]
    for pat in patterns:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def extract_pinpoint(full_citation: str) -> str:
    """Extract the pinpoint portion after the base citation.

    Primary extraction: match the base citation via ``_CITATION_REGEX``,
    return everything after it (stripped of leading comma/space).

    Examples:
        "Criminal Code, RSC 1985, c C-46, s 718.2(e)"  →  "s 718.2(e)"
        "Criminal Code, RSC 1985, c C-46."             →  ""
        "RSC 1985, c C-46"                              →  ""
        ""                                              →  ""

    Returns:
        The pinpoint string, or ``""`` if none found.
    """
    if not full_citation:
        return ""
    m = _CITATION_REGEX.search(full_citation)
    if not m:
        return ""
    remainder = full_citation[m.end():].strip().lstrip(",").strip()
    return remainder
