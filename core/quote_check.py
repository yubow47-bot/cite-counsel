"""Check a quotation against the judgment itself and find its paragraph.

A2AJ serves the (unofficial) full text of the decisions it indexes. When a
citation on screen came from that database, a quotation the user wants to use
can be looked up verbatim in the same record, and the paragraph marker above
it ("[47]") gives the pinpoint. The paragraph number is then copied from the
database text, not typed by the user or guessed by a model, so the citation
stays verified with the pinpoint in it.

Matching is literal. Only typography that word processors change silently is
normalized (curly quotes, dashes, soft hyphens, whitespace); case is compared
separately and reported, because McGill requires an exact quotation. An
ellipsis splits the quotation into parts that must all appear, in order,
within a few paragraphs of each other.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from core.tool_contracts import Derivation, Field, Finding

RULE_ID = "quote.locate.a2aj.v1"
MIN_QUOTE, MAX_QUOTE = 12, 2000
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "–": "-", "—": "-", "‑": "-", " ": " ", " ": " "})
_ELLIPSIS = re.compile(r"\s*(?:\.\s?\.\s?\.|…)\s*")
_PARAGRAPH = re.compile(r"(?:^|\n)\[(\d{1,4})\]\s")


def normalize(text: str) -> str:
    """Typography-only normalization; every character keeps its meaning."""
    text = (text or "").replace("­", "").translate(_TYPOGRAPHY)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r" ?\n[ \n]*", "\n", text).strip()


def quote_parts(quote: str) -> list[str]:
    """The user's quotation without enclosing quote marks, split at ellipses."""
    quote = normalize(quote).replace("\n", " ")
    quote = re.sub(r'^[\s"\']+|[\s"\']+$', "", quote)
    return [part.strip() for part in _ELLIPSIS.split(quote) if part.strip()]


# ── Judgment text (cached per citation) ───────────────────────────────


_cache: OrderedDict[str, dict | None] = OrderedDict()
_cache_lock = Lock()


def judgment(citation: str) -> dict | None:
    """{"text", "url", "citations"} for exactly this citation, or None."""
    with _cache_lock:
        if citation in _cache:
            _cache.move_to_end(citation)
            return _cache[citation]
    from local_tools.a2aj_api import A2AJ_BASE, _same_citation
    from local_tools.utils import a2aj_session, request_with_retry
    response = request_with_retry(a2aj_session, "GET", f"{A2AJ_BASE}/fetch",
                                  params={"citation": citation, "doc_type": "cases"}, read_timeout=20)
    response.raise_for_status()
    results = (response.json() or {}).get("results") or []
    found = None
    for record in results:
        if not isinstance(record, dict):
            continue
        citations = [c for c in (record.get("citation_en"), record.get("citation2_en")) if isinstance(c, str) and c]
        text = record.get("unofficial_text_en")
        # Only the record for this very citation may answer for it.
        if isinstance(text, str) and text.strip() and any(_same_citation(c, citation) for c in citations):
            found = {"text": normalize(text), "url": record.get("url_en") or "", "citations": citations}
            break
    with _cache_lock:
        _cache[citation] = found
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return found


# ── Locating ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Located:
    verdict: str            # "exact", "case_differs", "not_found"
    start: int = -1
    end: int = -1
    paragraphs: tuple[int, ...] = ()


def _find_parts(haystack: str, parts: list[str]) -> tuple[int, int] | None:
    """All parts in order, the whole span no longer than a few paragraphs."""
    position, start = 0, None
    for part in parts:
        index = haystack.find(part, position)
        if index < 0:
            return None
        start = index if start is None else start
        position = index + len(part)
    if start is None or position - start > 6000:
        return None
    return start, position


def paragraphs_between(text: str, start: int, end: int) -> tuple[int, ...]:
    """The numbered paragraph(s) a span sits in, from the "[n]" markers."""
    numbers, current, last = [], None, None
    for match in _PARAGRAPH.finditer(text):
        if match.start() >= end:
            break
        number = int(match.group(1))
        # Paragraph numbers run 1, 2, 3...; a line opening with "[2022] 3 SCR"
        # is a citation in the header, not paragraph 2022.
        if not (number == 1 or (last is not None and last < number <= last + 3)):
            continue
        last = number
        if match.start() <= start:
            current, numbers = number, []
        elif current is not None:
            numbers.append(number)
    if current is None:
        return ()
    return (current, *numbers)


def locate(text: str, quote: str) -> Located:
    parts = quote_parts(quote)
    if not parts:
        return Located("not_found")
    flat = text.replace("\n", " ")  # same length, so offsets line up with ``text``
    span = _find_parts(flat, parts)
    verdict = "exact"
    if span is None:
        span = _find_parts(flat.casefold(), [part.casefold() for part in parts])
        verdict = "case_differs"
    if span is None:
        return Located("not_found")
    return Located(verdict, span[0], span[1], paragraphs_between(text, *span))


def pinpoint_for(paragraphs: tuple[int, ...]) -> str:
    if not paragraphs:
        return ""
    first, last = paragraphs[0], paragraphs[-1]
    return f"at para {first}" if first == last else f"at paras {first}–{last}"


# ── The check, as a result block ──────────────────────────────────────


def case_citation(item: dict) -> str:
    """A citation the database itself supplied for this item, or ""."""
    if item.get("source_type") != "jurisprudence" or item.get("base") is not None:
        return ""
    for name in ("neutral_citation", "reporter"):
        field = item["fields"].get(name) or {}
        if field.get("origin") == "database" and field.get("value"):
            return field["value"]
    return ""


def check(item: dict, quote: str, *, quoted: Field | None = None, text: str | None = None,
          source_id: str | None = None) -> dict:
    """Locate ``quote`` in the judgment behind ``item``.

    By default the judgment text is fetched from the database by the
    citation ``item`` carries. Callers that already hold the full text --
    the harness ``quote`` plugin reads the stored ``full_text`` field -- pass
    ``text`` (and its ``source_id``) instead: the source Field then names
    exactly the stored evidence, so the provenance audit can match it.

    ``quoted`` is the quotation's own provenance Field -- the user's words,
    or ``model``-origin when the model supplied the quote. It defaults to a
    user field for direct callers (scripts, tests); the harness path always
    resolves it honestly.

    Returns {"verdict", "pinpoint", "excerpt", "match": [start, end] within
    the excerpt, "url", "finding"}. ValueError explains why no check ran.
    """
    quote = (quote or "").strip()
    if not MIN_QUOTE <= len(quote) <= MAX_QUOTE:
        raise ValueError(f"请粘贴 {MIN_QUOTE}–{MAX_QUOTE} 个字符的引语。")
    citation = case_citation(item)
    if not citation:
        raise ValueError("只有从判例数据库取回的判决可以对照原文核对引语。")
    if text is None:
        found = judgment(citation)
        if found is None:
            raise ValueError("数据库没有这份判决的全文，无法核对。")
        text, url = found["text"], found["url"]
        source_id = source_id or url or f"a2aj:{citation}"
    located = locate(text, quote)
    source = Field(text, "database", source_id=source_id or f"a2aj:{citation}")
    quoted = quoted or Field(quote, "user")
    if located.verdict == "not_found":
        finding = Finding("contradicted", "The quotation does not appear in the unofficial full text.",
                          "complete", Derivation((quoted, source), RULE_ID, ("quote", "source")))
        return {"verdict": "not_found", "pinpoint": "", "excerpt": "", "match": None,
                "url": source_id, "finding": finding}
    lo, hi = max(0, located.start - 220), min(len(text), located.end + 220)
    # Start and end the excerpt on word boundaries.
    while 0 < lo < located.start and not text[lo - 1].isspace():
        lo += 1
    while located.end < hi < len(text) and not text[hi].isspace():
        hi -= 1
    excerpt = text[lo:hi]
    verdict = "confirmed" if located.verdict == "exact" else "inconclusive"
    detail = ("The quotation appears verbatim in the unofficial full text." if verdict == "confirmed" else
              "The words appear, but capitalization differs from the text.")
    finding = Finding(verdict, detail, "complete", Derivation((quoted, source), RULE_ID, ("quote", "source")))
    return {"verdict": located.verdict, "pinpoint": pinpoint_for(located.paragraphs), "excerpt": excerpt,
            "match": [located.start - lo, located.end - lo], "url": source_id, "finding": finding}
