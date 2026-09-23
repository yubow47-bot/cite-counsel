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
    with _RULES_PATH.open(encoding="utf-8") as source:
        return json.load(source)["_chatbox_templates_v1"]


@lru_cache(maxsize=1)
def court_abbreviations() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as source:
        table = json.load(source)["_chatbox_court_abbreviations_v1"]
    return {name: value for name, value in table.items() if not name.startswith("_")}


def schema_fields(source_type: str) -> list[dict]:
    schema = schemas().get(source_type)
    return list(schema["fields"]) if schema else []


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
    parts += ["或".join(labels[key] for key in options if key in labels)
              for options in schema.get("required_any", []) if not filled & set(options)]
    return "、".join(parts)


def render_fields(source_type: str, fields: dict) -> str:
    """Literal segments + provided field values only. No guessed defaults."""
    schema = schemas().get(source_type)
    if schema is None:
        raise ValueError("此类型尚未支持确定性组装。")
    allowed = {f["name"] for f in schema["fields"]}
    if set(fields) - allowed:
        raise ValueError("包含此类型不支持的字段。")
    clean = {}
    for key, value in fields.items():
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("字段必须是 2000 字以内的文本。")
        clean[key] = value.strip()
    gap = missing_summary(source_type, clean)
    if gap:
        raise ValueError("请补充：" + gap)
    if source_type == "website":
        parsed = urlsplit(clean["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("请填写完整的 HTTP(S) URL。")
    segments = list(schema["segments"])
    anchor = next((name for name in schema.get("pinpoint_after", []) if clean.get(name)), None)
    if anchor and clean.get("pinpoint"):
        # The pinpoint belongs to the first citation, ahead of parallel ones.
        pinpoint = next(segment for segment in segments if segment[0] == "pinpoint")
        segments.remove(pinpoint)
        position = next(i for i, segment in enumerate(segments) if segment[0] == anchor)
        segments.insert(position + 1, pinpoint)
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
    elif name == "date":
        iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", value)
        if iso and 1 <= int(iso[2]) <= 12:
            value = f"{int(iso[3])} {_MONTHS[int(iso[2]) - 1]} {iso[1]}"
    return value
