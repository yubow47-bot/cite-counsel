"""Check quotations against stored text and locate numbered paragraphs.

Without supplied text, fetch the unofficial judgment identified by a database
citation. With supplied text, preserve its database or extracted origin and
exact value in the proof. A literal match establishes an occurrence in that
material, not the source's authority or the quotation's legal meaning.

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

RULE_ID = "quote.locate.material.v2"
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


def judgment(citation: str, *, refresh: bool = False) -> dict | None:
    """{"text", "url", "citations"} for exactly this citation, or None."""
    with _cache_lock:
        if not refresh and citation in _cache:
            _cache.move_to_end(citation)
            return _cache[citation]
    from local_tools.a2aj_api import _request_results, _same_citation
    from local_tools.source_status import SourceRequestError
    results = _request_results("/fetch", {"citation": citation, "doc_type": "cases"})
    found = None
    for record in results:
        if not isinstance(record, dict):
            continue
        citations = [c for c in (record.get("citation_en"), record.get("citation2_en")) if isinstance(c, str) and c]
        text = record.get("unofficial_text_en")
        # Only the record for this very citation may answer for it.
        if isinstance(text, str) and text.strip() and any(_same_citation(c, citation) for c in citations):
            found = {"text": normalize(text), "url": record.get("url_en") or "", "citations": citations,
                     "coverage": results.coverage}
            break
    if found is None and results.coverage.get("status") != "complete":
        raise SourceRequestError("a2aj", "/fetch cases", reason="incomplete_response", coverage=results.coverage)
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
    if not parts:
        return None
    start = haystack.find(parts[0])
    while start >= 0:
        position = start + len(parts[0])
        for part in parts[1:]:
            index = haystack.find(part, position, start + 6000)
            if index < 0:
                break
            position = index + len(part)
        else:
            if position - start <= 6000:
                return start, position
        # The first occurrence may be too far from the next part. A later
        # occurrence can still form a valid quotation with that same next part.
        start = haystack.find(parts[0], start + 1)
    return None


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
        folded_parts, offsets = [], []
        for index, char in enumerate(flat):
            folded = char.casefold()
            folded_parts.append(folded)
            offsets.extend([index] * len(folded))
        span = _find_parts("".join(folded_parts), [part.casefold() for part in parts])
        if span is not None:
            span = offsets[span[0]], offsets[span[1] - 1] + 1
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
          source_id: str | None = None, text_origin: str = "database", coverage: str = "complete") -> dict:
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
        raise ValueError(f"Paste a quotation of {MIN_QUOTE}–{MAX_QUOTE} characters.")
    citation = case_citation(item)
    if text is None:
        if not citation:
            raise ValueError("Only a judgment fetched from a case database can be checked against its own text.")
        found = judgment(citation)
        if found is None:
            raise ValueError("The database has no full text for this judgment, so it cannot be checked.")
        text, url = found["text"], found["url"]
        source_id = source_id or url or f"a2aj:{citation}"
    if text_origin not in ("database", "extracted"):
        raise ValueError("Quote checks require stored database or extracted source text.")
    if coverage not in ("complete", "partial"):
        raise ValueError("Unknown text coverage.")
    # Preserve the exact evidence value for the store's audit; normalization is
    # only for matching and displayed excerpts, never an origin upgrade.
    source = Field(text, text_origin, source_id=source_id)
    text = normalize(text)
    located = locate(text, quote)
    quoted = quoted or Field(quote, "user")
    if located.verdict == "not_found":
        finding = Finding("contradicted" if coverage == "complete" else "inconclusive",
                          "The quotation does not appear in the stored text" +
                          ("; extraction coverage is partial." if coverage == "partial" else "."),
                          coverage, Derivation((quoted, source), RULE_ID, ("quote", "source")))
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
    detail = ("The quotation appears verbatim in the stored text." if verdict == "confirmed" else
              "The words appear, but capitalization differs from the text.")
    finding = Finding(verdict, detail, coverage, Derivation((quoted, source), RULE_ID, ("quote", "source")))
    return {"verdict": located.verdict, "pinpoint": pinpoint_for(located.paragraphs), "excerpt": excerpt,
            "match": [located.start - lo, located.end - lo], "url": source_id, "finding": finding}
