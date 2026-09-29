"""A McGill bibliography assembled from citations the server itself produced.

Each entry is a plain snapshot of a citation's fields with their origins,
handed over by the harness record store (``ctx.records.meta(ref)``): the
store is server-owned and session-scoped, and references are the only
handle the model ever gets, so there is nothing for a client to forge and
nothing to sign. A bibliography is rebuilt from those snapshots, so an
entry's verification status is always re-derived from the field origins.

The structure (sections, sort keys, first-author inversion, no pinpoints)
is read from ``mcgill_rules.json`` like every other rule; the entries are
rendered by the same templates as the footnote citations.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from core.tool_contracts import Artifact, Derivation, Field, is_grounded

MAX_ENTRIES = 200
# Corporate authors are listed as written, never turned into "Association, Canadian Bar".
_ORGANIZATION = re.compile(
    r"\b(Association|Institute|Commission|Council|Canada|Government|Ministry|Department|University|"
    r"Society|Committee|Organi[sz]ation|Agency|Office|Bureau|Court|Foundation|Cent(?:re|er)|Inc|Ltd|"
    r"News|Press|Parliament|Senate|Nations|Bank|Board|Tribunal|Service|Group|Network)\b", re.I)


@lru_cache(maxsize=1)
def rules() -> dict:
    path = Path(__file__).resolve().parent.parent / "mcgill_rules.json"
    with path.open(encoding="utf-8") as source:
        return json.load(source)["_chatbox_bibliography_v1"]


# ── Entries ───────────────────────────────────────────────────────────

# An entry handed to ``build`` is a plain snapshot:
# {"source_type": str, "fields": {name: {"value", "origin", "source_id"?}}, "base": str | None}
# The harness store owns these; nothing arrives from the client.

# ── Rendering ─────────────────────────────────────────────────────────


def invert_first_author(author: str) -> str:
    """"Patrick Olivelle & Jane Doe" -> "Olivelle, Patrick & Jane Doe" (first author only).

    The surname is taken as the last word of the first name. That is the
    common case; compound surnames ("van der Berg") need a manual edit.
    """
    author = (author or "").strip()
    match = re.match(r"^(.*?)(,\s|\s&\s|\set al\b|$)(.*)$", author, re.S)
    head, sep, rest = match.group(1).strip(), match.group(2), match.group(3)
    words = head.split()
    if len(words) < 2 or _ORGANIZATION.search(head):
        return author
    inverted = words[-1] + ", " + " ".join(words[:-1])
    return inverted + (sep + rest if sep else "")


def _plain(text: str) -> str:
    return text.replace("*", "")


def _sort_key(text: str) -> str:
    folded = unicodedata.normalize("NFKD", _plain(text))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    return re.sub(r"^(the|a|an)\s+", "", re.sub(r"[^\w\s]", "", folded).strip())


def render_entry(entry: dict) -> tuple[str, str, str | None]:
    """(entry text with *italic* markers, sort key, section id or None for residual)."""
    from core.mcgill_format import render_fields
    config = rules()
    source_type = entry.get("source_type") or ""
    section = next((s for s in config["sections"] if source_type in s["source_types"]), None)
    values = {name: field["value"] for name, field in entry["fields"].items()
              if name not in config["omit_fields"]}
    if entry.get("base") is not None:
        # An opaque string from the original formatter: listed as issued.
        text = re.sub(r"\s+", " ", entry["base"]).strip()
        return text, _sort_key(text), section["id"] if section else None
    if section and section.get("invert_first_author") and values.get("author"):
        values["author"] = invert_first_author(values["author"])
    text = render_fields(source_type, values)
    key_field = section.get("sort_field") if section else None
    # Same author (or same style of cause): then by title, ignoring "The"/"A".
    key = _sort_key(values.get(key_field) or text) + "\x00" + _sort_key(values.get("title") or text)
    return text, key, section["id"] if section else None


def entry_verified(entry: dict) -> bool:
    """Re-derived for the bibliography form, from the signed per-field origins.

    The footnote was unverified if the user typed its pinpoint; the
    bibliography omits pinpoints, so what is left may be all database fields.
    Any other user- or extraction-supplied field still makes it unverified,
    and an opaque legacy string never counts as verified.
    """
    if entry.get("base") is not None:
        return False
    omitted = set(rules()["omit_fields"])
    kept = [field for name, field in entry["fields"].items() if name not in omitted]
    return bool(kept) and all(field.get("origin") == "database" for field in kept)


def build(entries: list) -> dict:
    """Sections in McGill order, alphabetical within each; duplicates collapsed."""
    if not isinstance(entries, list) or not entries:
        raise ValueError("The citation list is empty.")
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"At most {MAX_ENTRIES} citations can be assembled at once.")
    config = rules()
    order = [s["id"] for s in config["sections"]] + [config["residual_section"]["id"]]
    titles = {s["id"]: s["title"] for s in config["sections"]}
    titles[config["residual_section"]["id"]] = config["residual_section"]["title"]
    grouped: dict[str, dict[str, dict]] = {section_id: {} for section_id in order}
    derivations = []
    for raw in entries:
        entry = raw
        if not isinstance(entry, dict) or not isinstance(entry.get("fields"), dict):
            raise ValueError("This citation entry is malformed; render the citation again before adding it.")
        text, key, section_id = render_entry(entry)
        section_id = section_id or config["residual_section"]["id"]
        verified = entry_verified(entry)
        existing = grouped[section_id].get(_plain(text))
        # The same source added twice (e.g. once with a pinpoint) is one entry.
        if existing is None or (verified and not existing["verified"]):
            grouped[section_id][_plain(text)] = {"text": text, "key": key, "verified": verified}
        fields = {name: Field(value=f["value"], origin=f["origin"], source_id=f.get("source_id"))
                  for name, f in entry["fields"].items() if name not in config["omit_fields"]}
        if entry.get("base") is not None:
            fields["legacy_base"] = Field(value=entry["base"], origin="extracted")
        derivations.append(Derivation(tuple(fields.values()), "cite.render.v1", tuple(fields)))
    sections = []
    for section_id in order:
        items = sorted(grouped[section_id].values(), key=lambda e: (e["key"], e["text"]))
        if items:
            sections.append({"id": section_id, "title": titles[section_id],
                             "entries": [{"text": e["text"], "verified": e["verified"]} for e in items]})
    plain = "\n\n".join(s["title"].upper() + "\n\n" + "\n".join(_plain(e["text"]) for e in s["entries"])
                        for s in sections)
    artifact = Artifact("bibliography", plain, Derivation(tuple(derivations), "doc.bibliography.mcgill.v1",
                                                          tuple(f"entry_{i}" for i in range(1, len(derivations) + 1))))
    total = sum(len(s["entries"]) for s in sections)
    unverified = sum(1 for s in sections for e in s["entries"] if not e["verified"])
    return {"sections": sections, "text": plain, "count": total, "unverified": unverified,
            "grounded": is_grounded(artifact.derivation), "artifact": artifact}
