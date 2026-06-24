"""Shared utility functions for citation processing."""

import re

# Matches a base legal citation like "RSC 1985, c C-46", "SC 2002, c 1", "SOR/2000-111"
_CITATION_REGEX = re.compile(
    r"(?:RSC|SC|SOR|RRO|O\sReg|BC\sReg|RLRQ)\s[^,]+(?:,\s*c\s[^,]+)?",
)


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
