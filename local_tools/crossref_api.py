"""CrossRef API: DOI extraction, metadata fetch, McGill citation assembly (no LLM)."""

import re
import requests

CROSSREF_HEADERS = {
    "User-Agent": "McGillCitationTool/0.1 (mailto:test@example.com)"
}


def extract_doi(text: str) -> str | None:
    """Extract DOI from text.

    Handles: 10.xxxx/xxx, doi:10.xxxx/xxx, https://doi.org/10.xxxx/xxx
    Uses strict ASCII character class [A-Za-z0-9._/-] to avoid matching
    non-ASCII chars and to naturally stop at spaces/commas.
    Returns the clean DOI string, or None if not found.
    """
    if not text:
        return None
    m = re.search(
        r'(?:doi\.org/|doi\s*:\s*)?(10\.\d{4,}/[A-Za-z0-9._/-]+)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1)
    return None


def fetch_crossref(doi: str) -> dict | None:
    """Fetch CrossRef metadata for a DOI. Returns the 'message' dict, or None."""
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(url, headers=CROSSREF_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json().get("message")
    except Exception:
        return None


def build_journal_citation(cr_data: dict) -> str:
    """Assemble a McGill-format journal citation from CrossRef data (no LLM).

    Format: Author et al, "Title", *Journal* (Year) Volume:Issue FirstPage.
    """
    # ── Authors ──
    authors = cr_data.get("author", [])
    if not authors:
        author_str = ""
    elif len(authors) == 1:
        a = authors[0]
        author_str = _format_author(a) + ", "
    elif len(authors) > 3:
        author_str = _format_author(authors[0]) + " et al, "
    else:
        names = [_format_author(a) for a in authors]
        author_str = ", ".join(names) + ", "

    # ── Title ──
    title_raw = cr_data.get("title", [""])[0]
    title_str = f'"{title_raw}"' if title_raw else ""

    # ── Journal (Markdown italic) ──
    journal_raw = cr_data.get("container-title", [""])
    if isinstance(journal_raw, list):
        journal_raw = journal_raw[0] if journal_raw else ""
    journal_str = f" *{journal_raw}*" if journal_raw else ""

    # ── Year ──
    date_parts = cr_data.get("published", {}).get("date-parts", [[None]])
    year = date_parts[0][0] if date_parts and date_parts[0] else ""

    # ── Volume:Issue ──
    volume = cr_data.get("volume", "") or ""
    issue = cr_data.get("issue", "") or ""
    if volume and issue:
        vol_iss = f" {volume}:{issue}"
    elif volume:
        vol_iss = f" {volume}"
    else:
        vol_iss = ""

    # ── First page ──
    page = cr_data.get("page", "")
    first_page = page.split("-")[0].strip() if page else ""
    page_str = f" {first_page}" if first_page else ""

    return f"{author_str}{title_str} ({year}){vol_iss}{journal_str}{page_str}."


def _format_author(author: dict) -> str:
    """Format a single author as 'Given Family'."""
    given = author.get("given", "") or ""
    family = author.get("family", "") or ""
    if given and family:
        return f"{given} {family}"
    return family or given or "Unknown"
