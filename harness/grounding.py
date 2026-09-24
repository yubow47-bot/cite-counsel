"""Harness-level reply check: every fact carries its source (§5.4).

Every paragraph of the model's prose -- whether or not any plugin was loaded
or called this turn -- is scanned for fact-shaped strings. A shape is
declared by the installed plugins (``fact_patterns``: citations, bill
numbers, DOIs, paragraph pins, years); the harness adds a few generic ones.
Each fact is matched against the session's evidence: stored record fields
first (so a fact links to the record it came from), then tool results, then
the user's own words. A match carries its source; a miss is reported as
unsourced. Nothing is hidden -- the prose the user reads is the prose the
model wrote. The model may be wrong in public, but it can no longer be wrong
invisibly, which is what paragraph-hiding used to allow.

"Grounded" here means traceable, not correct.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

SCAN_LIMIT = 20000  # a runaway guard, not a style rule

# Generic shapes every session checks, whatever plugins say.
_GENERIC_SHAPES = (
    re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b"),                  # a year
    re.compile(r"\b\d{4}\s+[A-Z][A-Za-z]{1,5}\s+\d+\b"),       # 2002 SCC 10
    re.compile(r"\[\d{4}\]\s*\d*\s*[A-Z]{2,6}\s*\d+"),         # [1999] 1 SCR 688
    re.compile(r"\b(?:para|paras|at|ss?|art|ch)\s*\.?\s*\d+", re.I),
    re.compile(r"\bc\s+[A-Z]-\d+\b", re.I),                    # c C-46
    re.compile(r"\b10\.\d{4,9}/\S+"),                          # a DOI
)


def fact_shapes(plugin) -> tuple[re.Pattern, ...]:
    """A plugin's declared patterns, compiled once per call (cheap)."""
    shapes = []
    for pattern in plugin.fact_patterns or ():
        try:
            shapes.append(pattern if isinstance(pattern, re.Pattern) else re.compile(pattern))
        except re.error:
            logger.warning("Plugin %s has an invalid fact pattern", plugin.name)
    return tuple(shapes)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _excerpt(text: str, at: int, length: int, pad: int = 60) -> str:
    """The match with a little context, clipped on whitespace where possible."""
    lo = max(0, at - pad)
    hi = min(len(text), at + length + pad)
    while 0 < lo < at and not text[lo - 1].isspace():
        lo += 1
    while at + length < hi < len(text) and not text[hi].isspace():
        hi -= 1
    return ("…" if lo > 0 else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def annotate_facts(text: str, index: list[dict], shapes=()) -> list[dict]:
    """Per-fact provenance for the model's prose, in text order.

    ``index`` entries: ``{"kind": "record"|"tool"|"user", "text": str}`` and,
    for records, ``{"ref", "field", "origin", "source_id"}``. A fact found in
    a record carries ``ref``/``field``/``origin``/``source_id`` and an
    excerpt; one found in a tool result or the user's words carries only the
    excerpt; one found nowhere is ``unsourced``. Nothing is removed from the
    text -- the annotation describes it, the user judges it. Spans are
    offsets into the scanned text (the first SCAN_LIMIT characters).
    """
    if not isinstance(text, str) or not text.strip():
        return []
    text = text[:SCAN_LIMIT]
    sources = [(source, _norm(source.get("text") or "")) for source in index]
    annotations: dict[str, dict] = {}
    order: list[str] = []
    for shape in (*_GENERIC_SHAPES, *shapes):
        for match in shape.finditer(text):
            fact = match.group(0)
            key = _norm(fact)
            if key in annotations:
                annotations[key]["spans"].append([match.start(), match.end()])
                continue
            entry = {"fact": fact, "verdict": "unsourced", "spans": [[match.start(), match.end()]],
                     "kind": None, "ref": None, "field": None, "origin": None,
                     "source_id": None, "excerpt": None}
            for source, haystack in sources:
                at = haystack.find(key)
                if at < 0:
                    continue
                entry.update(verdict="sourced", kind=source["kind"],
                             excerpt=_excerpt(haystack, at, len(key)))
                if source["kind"] == "record":
                    entry.update(ref=source.get("ref"), field=source.get("field"),
                                 origin=source.get("origin"), source_id=source.get("source_id"))
                break
            annotations[key] = entry
            order.append(key)
    return [annotations[key] for key in order]
