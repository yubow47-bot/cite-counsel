"""Checks that a model's free text does not state a fact-shaped claim on its own.

Used as the default plugin ``reply_guard`` (see ``harness.plugin``): a value
shaped like a citation, a year, a pinpoint or a DOI must already appear
somewhere in the session (the user's own words, or a tool result) or the
whole reply is dropped.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 300

_FACT_SHAPES = (
    re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b"),                  # a year
    re.compile(r"\b\d{4}\s+[A-Z][A-Za-z]{1,5}\s+\d+\b"),       # 2002 SCC 10
    re.compile(r"\[\d{4}\]\s*\d*\s*[A-Z]{2,6}\s*\d+"),         # [1999] 1 SCR 688
    re.compile(r"\b(?:para|paras|at|ss?|art|ch)\s*\.?\s*\d+", re.I),
    re.compile(r"\bc\s+[A-Z]-\d+\b"),                          # c C-46
    re.compile(r"\b10\.\d{4,9}/\S+"),                          # a DOI
)


def unsupported_facts(message: str, grounded: str) -> list[str]:
    """Fact-shaped strings in *message* that do not already appear in *grounded*."""
    haystack = re.sub(r"\s+", " ", grounded).casefold()
    found = []
    for shape in _FACT_SHAPES:
        for match in shape.finditer(message):
            token = re.sub(r"\s+", " ", match.group(0)).casefold()
            if token not in haystack:
                found.append(match.group(0))
    return found


def safe_message(message, grounded: str) -> str:
    """The model's text, or "" when it carries a fact not found in *grounded*."""
    if not isinstance(message, str):
        return ""
    message = re.sub(r"\s+", " ", message).strip()
    if not message or len(message) > MESSAGE_LIMIT:
        return ""
    leaked = unsupported_facts(message, grounded)
    if leaked:
        logger.warning("Dropped a reply carrying ungrounded facts: %s", leaked)
        return ""
    return message


def grounded_reply_guard(text: str, ctx) -> str:
    """The default plugin ``reply_guard``: no year, citation or claim absent from this session.

    "Grounded" means the user's own messages plus every tool result shown this
    session -- not the model's own earlier prose, which could otherwise
    launder an invented fact into "already said, so it's fine now".
    """
    grounded = " ".join(ctx.user_texts() + [m.get("content") or "" for m in ctx.session.messages
                                            if m.get("role") == "tool"])
    return safe_message(text, grounded)
