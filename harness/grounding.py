"""Harness-level reply check: no fact without a source in this session (§5.4).

Every paragraph of the model's prose -- whether or not any plugin was loaded
or called this turn -- is scanned for fact-shaped strings. A shape is
declared by the installed plugins (``fact_patterns``: citations, bill
numbers, DOIs, paragraph pins, years); the harness adds a few generic ones.
A fact that cannot be found in the session's ground (the user's own words
plus every tool result shown this session) hides the whole paragraph that
carries it, and the user is told so.

"Grounded" here means traceable, not correct. Plugins may also keep their
own stricter ``reply_guard`` on top; the harness check runs for every
enabled plugin's patterns regardless of load state.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 4000  # a runaway guard, not a style rule: paragraphs are checked one by one

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


def unsupported_facts(message: str, grounded: str, shapes=()) -> list[str]:
    """Fact-shaped strings in *message* that do not already appear in *grounded*."""
    haystack = re.sub(r"\s+", " ", grounded).casefold()
    found = []
    for shape in (*_GENERIC_SHAPES, *shapes):
        for match in shape.finditer(message):
            token = re.sub(r"\s+", " ", match.group(0)).casefold()
            if token not in haystack:
                found.append(match.group(0))
    return found


def grounded_text(text: str, grounded: str, shapes=()) -> str:
    """The prose with every paragraph carrying an unsourced fact removed.

    A paragraph is a non-empty line or sentence run; hiding is per paragraph
    so one bad sentence does not silence an otherwise clean reply.
    """
    if not isinstance(text, str):
        return ""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text or len(text) > MESSAGE_LIMIT:
        return ""
    kept = []
    for paragraph in re.split(r"\n+|(?<=[。！？.!?])\s+", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        leaked = unsupported_facts(paragraph, grounded, shapes)
        if leaked:
            logger.warning("Dropped a paragraph carrying ungrounded facts: %s", leaked)
        else:
            kept.append(paragraph)
    return " ".join(kept)
