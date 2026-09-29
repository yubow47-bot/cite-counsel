"""CrossRef API: DOI extraction, metadata fetch, McGill citation assembly (no LLM)."""

import re
from local_tools.utils import crossref_session, request_with_retry
from profiling import timing as prof

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
        with prof.measure("http.crossref", endpoint="api.crossref.org"):
            resp = request_with_retry(crossref_session, "GET", url, headers=CROSSREF_HEADERS, read_timeout=15)
        resp.raise_for_status()
        return resp.json().get("message")
    except Exception:
        return None

