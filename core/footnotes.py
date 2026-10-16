"""Deterministic full / Ibid / supra resolution for an ordered set of footnotes.

Adapted from the stateless academic case-footnote preview in the earlier
McGill project. Inputs are the current ordered notes, the document's short
forms and a callback that renders one source's full citation with a given
pinpoint. Note numbers are always derived from the current order; nothing
stored ever says "note 3". No model, network or reverse parsing of a
formatted citation is involved.

Templates follow the McGill Guide's academic forms for cases. They are kept
here as one versioned table so a rules review can change them in one place.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

RULE_VERSION = "footnotes.academic.cases.v1"
TEMPLATES = {
    "full": "{citation}.",
    "full_short_form": "{citation} [{short_form}].",
    "ibid": "*Ibid*{pinpoint_suffix}.",
    "supra": "{short_form}, *supra* note {note_number}{pinpoint_suffix}.",
}
MAX_NOTES = 200
MAX_CITATIONS_PER_NOTE = 10


def clean_pinpoint(value) -> str:
    """A pinpoint as text, e.g. "at para 64"; empty means the whole source."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 100 or any(ord(c) < 32 for c in value) \
            or any(c in value for c in "*[]<>"):
        raise ValueError("A pinpoint must be plain text of at most 100 characters, e.g. 'at para 64'.")
    value = " ".join(value.split())
    numbers = [int(number) for number in re.findall(r"\d+", value)]
    if len(numbers) == 2 and "-" in value and numbers[0] > numbers[1]:
        raise ValueError("A pinpoint range must run from the lower number to the higher number.")
    return value if value.lower().startswith("at ") else "at " + value


def short_form_key(label: str) -> str:
    return " ".join(label.casefold().split())


def resolve(notes: list[dict], short_forms: dict[str, str], render: Callable[[str, str], str]) -> list[dict]:
    """Render ``notes`` in order.

    ``notes``: ``[{"id", "citations": [{"source", "pinpoint"}]}]``.
    ``short_forms``: source key -> plain short form (italicised here).
    ``render(source, pinpoint)``: the full citation text without a final period.

    Ibid is used only when this note and the one before each cite exactly one
    source, the same one. A whole-source reference after a pinpointed one is
    written in full rather than inheriting the old pinpoint. Supra needs a
    short form and always points at the note holding the first full citation.
    """
    if len(notes) > MAX_NOTES:
        raise ValueError(f"A document supports up to {MAX_NOTES} footnotes.")
    seen_forms = {}
    for source, label in short_forms.items():
        key = short_form_key(label)
        if key in seen_forms and seen_forms[key] != source:
            raise ValueError("Short forms must distinguish different sources.")
        seen_forms[key] = source
    # A short form is established only for a source cited in more than one note.
    in_notes = Counter(source for note in notes for source in {c["source"] for c in note["citations"]})
    chosen = {source: f"*{label}*" for source, label in short_forms.items() if in_notes[source] > 1}

    first_full, previous, rendered = {}, [], []
    for number, note in enumerate(notes, 1):
        pieces, pending = [], {}
        refs = note["citations"]
        for ref in refs:
            source, pin = ref["source"], ref.get("pinpoint", "")
            target = None
            kind = "full"
            if len(refs) == 1 and len(previous) == 1 and previous[0]["source"] == source \
                    and not (not pin and previous[0].get("pinpoint")):
                kind, target = "ibid", number - 1
                suffix = "" if pin == previous[0].get("pinpoint", "") else f" {pin}"
                text = TEMPLATES["ibid"].format(pinpoint_suffix=suffix)
            elif source in first_full and source in chosen:
                kind, target = "supra", first_full[source]
                text = TEMPLATES["supra"].format(short_form=chosen[source], note_number=target,
                                                 pinpoint_suffix=f" {pin}" if pin else "")
            if kind == "full":
                establish = source in chosen and source not in first_full and source not in pending
                citation = render(source, pin).removesuffix(".")
                text = (TEMPLATES["full_short_form"].format(citation=citation, short_form=chosen[source])
                        if establish else TEMPLATES["full"].format(citation=citation))
                pending.setdefault(source, number)
            pieces.append({"source": source, "pinpoint": pin, "kind": kind, "text": text, "target_note": target})
        for source, first in pending.items():
            first_full.setdefault(source, first)
        rendered.append({"id": note["id"], "number": number,
                         "text": "; ".join(p["text"].removesuffix(".") for p in pieces) + ("." if pieces else ""),
                         "citations": pieces})
        previous = refs
    return rendered
