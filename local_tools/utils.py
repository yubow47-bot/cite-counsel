"""Shared utility functions for citation processing."""

import re
import time

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
discord_session = _requests.Session()
generic_session = _requests.Session()


# ── Shared retry helper for external HTTP calls ──────────────────────────────
#
# Provides split connect/read timeouts and a single automatic retry on
# transient network errors (connection drops, DNS resolution failures,
# server-side timeouts).  Does NOT retry on HTTP error status codes.
def request_with_retry(
    session,
    method,
    url,
    connect_timeout=2.7,
    read_timeout=30,
    retries=1,
    backoff=0.3,
    **kwargs,
):
    """Call ``session.request(method, url, timeout=(connect_timeout,
    read_timeout), **kwargs)``.  On ``requests.exceptions.ConnectionError`` or
    ``requests.exceptions.Timeout``, retry up to ``retries`` additional times
    with ``backoff`` seconds between attempts.  Does **not** retry on HTTP
    error status codes (caller remains responsible for ``raise_for_status()``).
    Re-raises the last exception if all attempts fail.

    Parameters
    ----------
    session : requests.Session
        The shared session to use.
    method : str
        HTTP method (``"GET"``, ``"POST"``, …).
    url : str
        Target URL.
    connect_timeout : float
        Seconds to wait for connection establishment.
    read_timeout : float
        Seconds to wait for a response once connected.
    retries : int
        Number of *additional* attempts after the first failure.
    backoff : float
        Seconds to sleep between attempts.
    **kwargs
        Passed verbatim to ``session.request()`` (``params``, ``json``,
        ``headers``, ``verify``, …).
    """
    timeout = (connect_timeout, read_timeout)
    last_exc = None

    for attempt in range(retries + 1):
        try:
            return session.request(method, url, timeout=timeout, **kwargs)
        except (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff)
        # Any other exception (HTTPError, ValueError, …) propagates immediately.

    raise last_exc  # type: ignore[misc]


# ── Statute / regulation citation matching ───────────────────────────────────
#
# The old pattern was ``(?:RSC|SC|SOR|RRO|O\sReg|BC\sReg|RLRQ)\s[^,]+(?:,\s*c\s[^,]+)?``.
# Two problems it caused:
#   * Only federal prefixes plus a handful of others.  Every provincial
#     citation (RSO, SO, RSA, SA, RSBC, SBC, CCSM, RSS, SNS, SNB, CQLR, …) was
#     unrecognised, so the legislation route fell through to a CanLII
#     title-only match that can never match a string containing a citation.
#   * ``[^,]+`` cannot cross a comma, and the optional tail required a ``c``
#     chapter, so regulation citations split mid-way:
#     ``extract_pinpoint("…, RRO 1990, Reg 194, r 21.01")`` returned
#     ``"Reg 194, r 21.01"`` — the regulation number leaked into the pinpoint.
#
# Built from explicit shapes instead.  Each prefix tolerates interior periods
# ("S.O." for "SO") because that is how citations are typed by hand.


def _dotted(letters: str) -> str:
    """``"RSO"`` → ``r"R\\.?\\s?S\\.?\\s?O\\.?"`` — tolerate "R.S.O." / "RSO"."""
    return r"\.?\s?".join(letters) + r"\.?"


def _alt(*prefixes: str) -> str:
    """Alternation of dotted prefixes, longest first so "RSO" wins over "SO"."""
    return "|".join(_dotted(p) for p in sorted(prefixes, key=len, reverse=True))


# Revised + annual statute volumes, federal and provincial/territorial.
_STATUTE_VOL_YEAR = _alt(
    "RSC", "SC",
    "RSO", "SO", "RSA", "SA", "RSBC", "SBC", "RSM", "SM",
    "RSS", "SS", "RSNS", "SNS", "RSNB", "SNB", "RSNL", "SNL", "RSN", "SN",
    "RSPEI", "SPEI", "RSY", "SY", "RSYT", "SYT",
    "RSNWT", "SNWT", "RSNu", "SNu", "SQ", "LQ",
)
# Consolidations with no year in the citation ("CQLR c C-25.01", "CCSM c F20").
_STATUTE_VOL_NOYEAR = _alt("CQLR", "RLRQ", "LRQ", "CCSM", "CPLM")

_YEAR = r"(?:1[89]|20)\d{2}"
_CHAPTER = r"c\.?\s*[A-Za-z0-9][\w.\-]*"
# "Sch 17" / "Schedule A" — part of the citation, not a pinpoint.
_SCHEDULE = r"(?:,\s*Sch(?:ed(?:ule)?)?\.?\s*[A-Za-z0-9][\w.\-]*)?"
# "(Supp)" / ", 5th Supp" supplement markers.
_SUPP = r"(?:,\s*(?:\d(?:st|nd|rd|th)\s+)?Supp\.?|\s*\((?:\d(?:st|nd|rd|th)\s+)?Supp\.?\))?"

_CITATION_ALTERNATIVES = [
    # "RSC 1985, c C-46" · "SO 2019, c 7, Sch 17" · "RSO 1990, c F.3"
    # The comma before the chapter is optional ("Courts of Justice Act RSO 1990
    # c C.43" is typed without it), and so is the chapter itself (a bare
    # "RSO 1990" still has to be recognised as citation text, not as title text).
    rf"(?:{_STATUTE_VOL_YEAR})\s*{_YEAR}(?:\s*,?\s*{_CHAPTER})?{_SCHEDULE}{_SUPP}",
    # "CQLR c C-25.01" · "CCSM c F20"
    rf"(?:{_STATUTE_VOL_NOYEAR})\s*{_CHAPTER}{_SCHEDULE}",
    # "SOR/2000-111" · "SI/2019-12" · "CRC, c 870"
    rf"(?:{_alt('SOR', 'SI')})\s*/\s*\d{{2,4}}\s*-\s*\d+",
    rf"(?:{_dotted('CRC')})(?:\s*,\s*{_CHAPTER})?",
    # "RRO 1990, Reg 194" — revised regulations with a year
    rf"(?:{_alt('RRO', 'RRS', 'RRNS', 'RRNB')})\s*{_YEAR}\s*,\s*Reg\.?\s*\d+",
    # "O Reg 194/90" · "BC Reg 168/2009" · "Alta Reg 124/2001" · "NS Reg 1/2000"
    rf"(?:{_alt('O', 'BC', 'NS', 'NB', 'PEI', 'NWT', 'Nu', 'YT')}|Alta|Sask|Man|Nfld)"
    rf"\s*Reg\.?\s*\d+\s*/\s*\d{{2,4}}",
]

# Matches a base legal citation like "RSC 1985, c C-46", "SO 2019, c 7",
# "CQLR c C-25.01", "SOR/2000-111", "RRO 1990, Reg 194", "O Reg 194/90".
_CITATION_REGEX = re.compile("|".join(f"(?:{a})" for a in _CITATION_ALTERNATIVES))


def extract_case_pinpoint(raw_input: str) -> str:
    """Extract a pinpoint from a case-name (case_route) query string.

    Recognizes trailing patterns at the END of the input:
      "at para N"       e.g. "at para 2"
      "at paras N-M"    e.g. "at paras 10-15"
      "at paras N"      e.g. "at paras 10"
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
        r'at\s+paras\s+\d+$',
        r'at\s+para\s+\d+(?:-\d+)?$',
        r'at\s+pp\s+\d+(?:-\d+)$',
        r'at\s+p\s+\d+(?:-\d+)?$',
        r'\bat\s+\d+$',
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
