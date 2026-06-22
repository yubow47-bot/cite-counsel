"""Open Library API: ISBN extraction, metadata fetch, McGill book citation assembly (no LLM)."""

import re
import requests
from profiling import timing as prof


def extract_isbn(text: str) -> str | None:
    """Extract ISBN-10 or ISBN-13 from text.

    Handles: "ISBN:" prefix, "ISBN-10:"/"ISBN-13:" prefix, bare numbers,
    with or without hyphens/spaces.  Returns the clean digits-only string
    (ISBN-10 may end with 'X').  ISBN-13 takes priority when both patterns
    appear.  Returns None if nothing found.
    """
    if not text:
        return None

    # Remove ISBN prefix tags (ISBN:, ISBN-10:, ISBN-13:, ISBN  etc.)
    cleaned = re.sub(r'(?i)\bISBN[- ]?(10|13)?[-:\. ]*\s*', '', text)
    # Remove hyphens only (keep spaces to avoid merging adjacent numbers)
    cleaned = re.sub(r'-', '', cleaned)

    # ISBN-13: exactly 13 digits, prefix 978 or 979 (standalone number)
    m = re.search(r'(?<!\d)(97[89]\d{10})(?!\d)', cleaned)
    if m:
        return m.group(1)

    # ISBN-10: exactly 10 chars — 9 digits + 1 check digit (digit or X)
    m = re.search(r'(?<!\d)(\d{9}[\dXx])(?!\d)', cleaned)
    if m:
        return m.group(1).upper()

    return None


def validate_isbn(isbn: str) -> bool:
    """Validate an ISBN-10 or ISBN-13 check digit.

    ISBN-13: mod-10 with alternating weights 1,3,1,3,...
    ISBN-10: mod-11 with weights 10,9,...,1; check digit may be 'X'.
    """
    if not isbn:
        return False
    # Strip hyphens and spaces before checking length
    raw = re.sub(r'[\s-]', '', isbn.strip())

    if len(raw) == 13 and raw.startswith(("978", "979")):
        return _validate_isbn13(raw)
    if len(raw) == 10:
        return _validate_isbn10(raw)
    return False


def _validate_isbn13(isbn: str) -> bool:
    """Check ISBN-13 check digit (mod-10, alternating 1/3 weight)."""
    if not re.match(r'^97[89]\d{10}$', isbn):
        return False
    digits = [int(c) for c in isbn]
    total = sum(d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
    check = (10 - (total % 10)) % 10
    return check == digits[12]


def _validate_isbn10(isbn: str) -> bool:
    """Check ISBN-10 check digit (mod-11, weight 10..1)."""
    if not re.match(r'^\d{9}[\dXx]$', isbn):
        return False
    raw = isbn.upper()
    total = 0
    for i, ch in enumerate(raw):
        weight = 10 - i
        digit = 10 if ch == "X" else int(ch)
        total += digit * weight
    return total % 11 == 0


def fetch_openlibrary(isbn: str) -> dict | None:
    """Fetch Open Library book metadata for an ISBN.

    GET https://openlibrary.org/api/books?bibkeys=ISBN:…&format=json&jscmd=data
    Returns the per-book dict, or None on any failure.
    """
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        with prof.measure("http.openlibrary", endpoint="openlibrary.org"):
            resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        key = f"ISBN:{isbn}"
        return data.get(key)
    except Exception:
        return None


def build_book_citation(ol_data: dict) -> str | None:
    """Assemble a McGill-format book citation from Open Library data (no LLM).

    Format: Author, Title, Edition (Place: Publisher, Year).

    ISBN is **never** included in the output.
    Returns None if any required field (author, title, publisher, year) is
    missing.  Missing place/edition are silently omitted.
    """
    # ── Authors ──
    authors = ol_data.get("authors", [])
    if not authors:
        return None
    author_names = [a.get("name", "").strip() for a in authors if a.get("name")]
    author_names = [n for n in author_names if n]
    if not author_names:
        return None

    if len(author_names) == 1:
        author_str = f"{author_names[0]}, "
    elif len(author_names) == 2:
        author_str = f"{author_names[0]} & {author_names[1]}, "
    elif len(author_names) == 3:
        author_str = f"{author_names[0]}, {author_names[1]} & {author_names[2]}, "
    else:
        author_str = f"{author_names[0]} et al, "

    # ── Title ──
    title = ol_data.get("title", "").strip()
    if not title:
        return None
    subtitle = ol_data.get("subtitle", "").strip()
    if subtitle:
        title = f"{title}: {subtitle}"

    # ── Edition (optional) ──
    edition = ol_data.get("edition_name", "").strip()
    edition_str = f", {edition}" if edition else ""

    # ── Place of publication (optional — degrade gracefully) ──
    # Open Library wraps uncertain places like [United States?]; strip brackets/?.
    places = ol_data.get("publish_places", [])
    place = places[0].get("name", "").strip() if places else ""
    if place:
        place = re.sub(r'^\[(.+)\]$', r'\1', place).replace('?', '')
    place_str = f"{place}: " if place else ""

    # ── Publisher ──
    publishers = ol_data.get("publishers", [])
    if not publishers:
        return None
    publisher = publishers[0].get("name", "").strip()
    if not publisher:
        return None

    # ── Year ──
    publish_date = ol_data.get("publish_date", "").strip()
    year = _extract_year(publish_date)
    if not year:
        return None

    return f"{author_str}*{title}*{edition_str} ({place_str}{publisher}, {year})."


def _extract_year(date_str: str) -> str | None:
    """Extract a 4-digit year from various date-string formats."""
    if not date_str:
        return None
    m = re.search(r"(\d{4})", date_str)
    return m.group(1) if m else None
