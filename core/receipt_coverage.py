"""Which harness execution receipts show that an exact passage was obtained or read.

Pure functions over the server-owned receipt table. A matching receipt shows
that a tool actually returned this exact record version; it never shows that
the passage is relevant or sufficient.
"""

from __future__ import annotations

from core import research

ACQUISITION = {"source", "extract"}


def bound_to(receipts, ref, fingerprint, categories):
    """Successful executed receipts in ``categories`` that returned this exact version."""
    return [row for row in receipts
            if row.get("category") in categories and row.get("status") == "ok"
            and row.get("executed") is not False
            and any(value.get("ref") == ref and value.get("fingerprint") == fingerprint
                    for value in row.get("refs", []) if isinstance(value, dict))]


def read_spans(matches, ref, field_name, value, start=0, end=None):
    """Hash-checked spans of ``value`` that read receipts returned.

    Each item is ``(left, right, sequence, receipt_id)``; ``sequence`` is the
    receipt's position in ``matches``. With ``end`` given, only spans that
    overlap ``[start, end)`` are kept.
    """
    spans = []
    for sequence, row in enumerate(matches):
        if row.get("category") != "read":
            continue
        for observation in row.get("observations", []):
            if not isinstance(observation, dict):
                continue
            left, right = observation.get("offset"), observation.get("end")
            if (observation.get("ref") == ref and observation.get("field") == field_name
                    and type(left) is int and type(right) is int
                    and 0 <= left < right <= len(value)
                    and (end is None or (left < end and right > start))
                    and observation.get("text_sha256") == research.digest(value[left:right])):
                spans.append((left, right, sequence, row["id"]))
    return spans


def covering_reads(spans, start, end):
    """Receipt IDs whose spans jointly cover ``[start, end)``, or None.

    Greedy: at each point take the span reaching furthest, preferring the
    later receipt on ties, so an unrelated latest read never displaces an
    older read that actually covers the passage.
    """
    covered, inspected = start, []
    while covered < end:
        choices = [span for span in spans if span[0] <= covered < span[1]]
        if not choices:
            return None
        best = max(choices, key=lambda span: (span[1], span[2]))
        covered = best[1]
        inspected.append(best[3])
    return inspected
