"""McGill (10th ed) citation templates and formatting rules from ``mcgill_rules.json``.

Pure rendering: given a source type and a mapping of field values, produce
the citation text. Nothing here decides where a field's value came from --
that is the caller's (a future ``mcgill`` plugin's) job, working against the
Field/Record contract in ``core.tool_contracts``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

_RULES_PATH = Path(__file__).resolve().parent.parent / "mcgill_rules.json"


@lru_cache(maxsize=1)
def schemas() -> dict:
    """Every McGill rule in the rules file that says how to render it, by type."""
    with _RULES_PATH.open(encoding="utf-8") as source:
        rules = json.load(source)
    return {name: rule["render"] for name, rule in rules.items()
            if isinstance(rule, dict) and "render" in rule}


@lru_cache(maxsize=1)
def court_abbreviations() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as source:
        table = json.load(source)["_chatbox_court_abbreviations_v1"]
    return {name: value for name, value in table.items() if not name.startswith("_")}


def schema_fields(source_type: str) -> list[dict]:
    schema = schemas().get(source_type)
    return list(schema["fields"]) if schema else []


# What a value must look like before it can be written into a record field.
# Shape only -- never where the value came from. (pattern, hint); the
# pattern must match the whole value. Fields not listed only get the
# generic length cap.
_ANY_DIGIT = r"(?=.*\d)"
_YEAR = (r"\d{4}", "a four-digit year, e.g. 2022")
_DATE = (r"(?=.*\d{4}).{4,40}", "a date with its year, e.g. 26 November 2005")
_PINPOINT = (_ANY_DIGIT + r".{1,40}", "a short pinpoint with a number, e.g. at para 64")
FIELD_FORMATS: dict[str, dict[str, tuple[str, str]]] = {
    "jurisprudence": {
        "neutral_citation": (r"\d{4} [A-Z][A-Za-z]{1,10} \d+", "year, court abbreviation, number, e.g. 2022 SCC 39"),
        "reporter": (r"[\[(]\d{4}[\])],? .{3,80}\d", "e.g. [1999] 1 SCR 688 or (1999), 145 CCC (3d) 1"),
        "court": (r"[A-Za-z][A-Za-z .&'’-]{1,79}", "a court name or abbreviation, e.g. Ont Sup Ct J"),
        "pinpoint": _PINPOINT,
    },
    "legislation": {"citation": (_ANY_DIGIT + r".{3,120}", "e.g. RSC 1985, c C-46"), "pinpoint": _PINPOINT},
    "by_law": {"number": (_ANY_DIGIT + r".{1,40}", "the by-law number, e.g. 2008-250"), "year": _YEAR},
    "treaty": {"date": _DATE, "citation": (_ANY_DIGIT + r".{3,120}", "e.g. 999 UNTS 171")},
    "foreign": {"citation": (_ANY_DIGIT + r".{3,120}", "the citation as the source gives it"), "year": _YEAR},
    "bill": {"number": (r"[A-Z]-\d{1,4}", "e.g. C-21"), "session": (r"\d{1,2}(st|nd|rd|th)?", "e.g. 1st"),
             "parliament": (r"\d{1,2}(st|nd|rd|th)?", "e.g. 44th"), "year": _YEAR},
    "news_online": {"date": _DATE, "page": (r"[A-Za-z]?\d{1,4}[A-Za-z]?", "e.g. A4"),
                    "site": (r"[\w.-]+\.[a-z]{2,}(/\S*)?", "a domain, e.g. news.bbc.co.uk")},
    "website": {"date": _DATE, "url": (r"https?://\S+", "a full http(s) URL")},
    "book": {"year": _YEAR, "edition": (r".{1,40}", "e.g. 2nd ed"), "pinpoint": _PINPOINT},
    "journal_article": {"year": _YEAR, "volume": (r"\d{1,4}[A-Za-z]?", "e.g. 45"),
                        "issue": (r"[\w./-]{1,10}", "e.g. 2"), "first_page": (r"\d{1,5}|[ivxlcdm]{1,8}", "e.g. 123"),
                        "pinpoint": (r"\d{1,5}(\s*[-–]\s*\d{1,5})?", "page digits only, e.g. 23")},
}
_MAX_FIELD = 300


def field_problem(source_type: str, name: str, value: str) -> str | None:
    """Why ``value`` cannot go into ``name`` on a ``source_type`` record, or None.

    Shape only: an unknown field, an over-long value, or one that does not
    look like what the field holds (a sentence in a citation-number field).
    """
    fields = {f["name"]: f for f in schema_fields(source_type)}
    if name not in fields:
        return f"{source_type} records take {', '.join(fields)}; not {name}."
    value = re.sub(r"\s+", " ", value or "").strip()
    if not value:
        return "the value is empty."
    if len(value) > _MAX_FIELD:
        return f"the value is longer than {_MAX_FIELD} characters."
    rule = FIELD_FORMATS.get(source_type, {}).get(name)
    if rule and not re.fullmatch(rule[0], value):
        return f"'{value}' does not look like a {name}; expected {rule[1]}."
    return None


def _filled(values: dict) -> set:
    return {name for name, value in values.items() if isinstance(value, str) and value.strip()}


def missing_required(source_type: str, values: dict) -> list[dict]:
    """Schema fields that still block a deterministic render, in asking order.

    A ``required_any`` group contributes all of its members: the user picks
    whichever one the source actually shows.
    """
    schema = schemas().get(source_type)
    if schema is None:
        return []
    filled = _filled(values)
    missing = [f for f in schema["fields"] if f.get("required") and f["name"] not in filled]
    for options in schema.get("required_any", []):
        if not filled & set(options):
            missing += [f for f in schema["fields"] if f["name"] in options and f not in missing]
    return missing


def missing_summary(source_type: str, values: dict) -> str:
    """The same gap, phrased for a person: required fields, then each either/or group."""
    schema = schemas().get(source_type) or {}
    filled = _filled(values)
    labels = {f["name"]: f["label"] for f in schema.get("fields", [])}
    parts = [f["label"] for f in schema.get("fields", []) if f.get("required") and f["name"] not in filled]
    parts += [" or ".join(labels[key] for key in options if key in labels)
              for options in schema.get("required_any", []) if not filled & set(options)]
    return ", ".join(parts)


def render_fields(source_type: str, fields: dict) -> str:
    """Literal segments + provided field values only. No guessed defaults."""
    schema = schemas().get(source_type)
    if schema is None:
        raise ValueError("This type has no deterministic assembly yet.")
    allowed = {f["name"] for f in schema["fields"]}
    if set(fields) - allowed:
        raise ValueError("Contains a field this type does not support.")
    clean = {}
    for key, value in fields.items():
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("Each field must be text of 2000 characters or fewer.")
        clean[key] = value.strip()
    gap = missing_summary(source_type, clean)
    if gap:
        raise ValueError("Still needed: " + gap)
    if source_type == "website":
        parsed = urlsplit(clean["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Please provide a full HTTP(S) URL.")
    segments = list(schema["segments"])
    anchor = next((name for name in schema.get("pinpoint_after", []) if clean.get(name)), None)
    if anchor and clean.get("pinpoint"):
        # The pinpoint belongs to the first citation, ahead of parallel ones.
        pinpoint = next(segment for segment in segments if segment[0] == "pinpoint")
        segments.remove(pinpoint)
        position = next(i for i, segment in enumerate(segments) if segment[0] == anchor)
        segments.insert(position + 1, pinpoint)
    # Whoever types a pinpoint often includes the "at" the template already
    # prints ("at para 64"). Strip it only where the template would double it;
    # a bare suffix template keeps the value as typed.
    if clean.get("pinpoint"):
        for key, prefix, _suffix in segments:
            if key == "pinpoint" and "at" in prefix:
                clean["pinpoint"] = re.sub(r"^at\s+", "", clean["pinpoint"], flags=re.I)
    text = "".join(prefix + clean[key] + suffix for key, prefix, suffix in segments if clean.get(key))
    return text if text.endswith(".") else text + "."


_MONTHS = ("January February March April May June July August September October November December").split()


def mcgill_clean(name: str, value: str) -> str:
    """Deterministic McGill conventions applied to a value copied from a source."""
    value = re.sub(r"\s+", " ", value).strip()
    if name in {"author", "title", "place", "publisher", "journal", "newspaper"}:
        value = value.rstrip(" .,;")
    if name == "author":
        # Bibliography order "Olivelle, Patrick" -> McGill "Patrick Olivelle" (single author only).
        parts = [part.strip() for part in value.split(",")]
        if len(parts) == 2 and all(parts) and not re.search(r"&|\band\b|\bet al\b", value, re.I):
            value = f"{parts[1]} {parts[0]}"
    if name == "style_of_cause":
        value = re.sub(r"\b(R|v|c)\.(?=\s)", r"\1", value)
        value = re.sub(r"\((?:the\s+)?Attorney General\)", "(AG)", value, flags=re.I)
        # "Canada AG" / "Ontario A.G." -> "Canada (AG)"; only Canadian jurisdictions,
        # so a company such as "Siemens AG" is left alone.
        value = re.sub(r"\b(Canada|Ontario|Qu[eé]bec|Alberta|Manitoba|Saskatchewan|Nova Scotia|New Brunswick|"
                       r"British Columbia|Newfoundland and Labrador|Prince Edward Island|Yukon|Nunavut|"
                       r"Northwest Territories)\s+(?:A\.\s?G\.?|AG)(?=[\s,]|$)", r"\1 (AG)", value)
    elif name == "court" and court_abbreviations().get(re.sub(r"^the\s+", "", value, flags=re.I).casefold()):
        value = court_abbreviations()[re.sub(r"^the\s+", "", value, flags=re.I).casefold()]
    elif name in {"neutral_citation", "reporter", "citation", "court"}:
        value = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z ,)\]]|$)", "", value)  # R.S.C. -> RSC, c. -> c
        value = re.sub(r"^([A-Z][A-Za-z]{1,5}),\s+(\d{4})", r"\1 \2", value)  # RSC, 1985 -> RSC 1985
        # Ontario revised-statute chapters are "letter.number" (c F.3); some
        # databases drop that period, which the rule above cannot tell apart.
        value = re.sub(r"^(RSO \d{4}, c [A-Z])(\d+)\b", r"\1.\2", value)
    elif name in {"session", "parliament"} and value.isdigit():
        # LEGISinfo gives 1 / 44; McGill writes 1st Sess, 44th Parl.
        from local_tools.legisinfo_api import ordinal_suffix
        value = ordinal_suffix(int(value))
    elif name == "date":
        iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", value)
        if iso and 1 <= int(iso[2]) <= 12:
            value = f"{int(iso[3])} {_MONTHS[int(iso[2]) - 1]} {iso[1]}"
    return value
