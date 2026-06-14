"""Deterministic citation scaffold — grounding fallback.

No LLM, no heuristics.  Pure template substitution.
Best-effort prefill extraction (year regex, route-based field assignment).

Templates and field configs are read dynamically from ``mcgill_rules.json``.
Top-level keys that have a ``fields`` array become scaffold ``type_options``.
Each such key's ``template`` + ``fields`` become the ``field_config`` entry.
"""

import json
import os
import re
from pathlib import Path

# ── Path to the rules JSON (source of truth) ──
_RULES_PATH = Path(__file__).resolve().parent.parent / "mcgill_rules.json"


# ═══════════════════════════════════════════════════════════════════
#  Dynamic readers
# ═══════════════════════════════════════════════════════════════════

def _load_rules() -> dict:
    """Load and return the full mcgill_rules.json."""
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_scaffold_types() -> list[str]:
    """Top-level rules keys that have a ``fields`` array (i.e. scaffoldable)."""
    rules = _load_rules()
    return [k for k, v in rules.items() if isinstance(v, dict) and "fields" in v and v["fields"]]


def get_type_options() -> list[dict]:
    """Return ``[{type, label}, …]`` for every scaffoldable type."""
    rules = _load_rules()
    options = []
    for key in get_scaffold_types():
        config = rules[key]
        label = config.get("category", key.replace("_", " ").title())
        options.append({"type": key, "label": label})
    return options


def get_field_configs() -> dict[str, dict]:
    """Return ``{type: {template, fields}, …}`` for every scaffoldable type."""
    rules = _load_rules()
    configs = {}
    for key in get_scaffold_types():
        config = rules[key]
        configs[key] = {
            "template": config.get("template", ""),
            "fields": config.get("fields", []),
        }
    return configs


# ═══════════════════════════════════════════════════════════════════
#  Classifier route → scaffold type mapping
# ═══════════════════════════════════════════════════════════════════

SCAFFOLD_ELIGIBLE_ROUTES: set[str] = {
    "case_name",
    "citation_number",
    "legislation",
    "concept",
    "bill",
}

SUGGESTED_TYPE_MAP: dict[str, str] = {
    "case_name": "jurisprudence",
    "citation_number": "jurisprudence",
    "legislation": "legislation",
    "concept": "jurisprudence",
    "bill": "bill",
}


# ═══════════════════════════════════════════════════════════════════
#  Assembly
# ═══════════════════════════════════════════════════════════════════

def assemble(citation_type: str, fields: dict) -> str:
    """Deterministic template fill — no LLM, no hallucination.

    Substitutes ``[key]`` placeholders in the template with values from *fields*.
    Unfilled placeholders are silently removed.
    """
    configs = get_field_configs()
    if citation_type not in configs:
        raise ValueError(f"Unknown scaffold type: {citation_type}")
    template = configs[citation_type]["template"]
    result = template
    for key, value in fields.items():
        if value is None:
            value = ""
        placeholder = f"[{key}]"
        if placeholder in result:
            result = result.replace(placeholder, str(value).strip())
    # Strip any remaining unfilled placeholders.
    # Only strip [alphanumeric_keys] — NOT [1986] (actual citation text).
    result = re.sub(r"\[[a-z][\w_-]*\]", "", result, flags=re.IGNORECASE)
    # Clean up → remove trailing space before punctuation
    result = re.sub(r" ,", ",", result)
    result = re.sub(r"\s+\.", ".", result)
    # Remove empty comma groups: ", ," → ""
    result = re.sub(r",\s*,", "", result)
    # Strip leading/trailing commas and spaces
    result = re.sub(r"^[,.\s]+", "", result)
    result = re.sub(r"[,.\s]+$", "", result)
    # Collapse multiple spaces
    result = re.sub(r"  +", " ", result).strip()
    result = re.sub(r"\.{2,}", ".", result)
    if not result.endswith("."):
        result += "."
    return result


# ═══════════════════════════════════════════════════════════════════
#  Prefill
# ═══════════════════════════════════════════════════════════════════

def extract_year(text: str) -> str | None:
    """Extract a 4-digit year from raw text."""
    m = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    return m.group(1) if m else None


def build_prefill(
    route: str, query: str, partial: dict | None = None
) -> dict:
    """Best-effort prefill from raw input (no LLM, no keyword heuristics).

    *route* is the classification result (case_name / legislation / …).
    *partial*, if given, is a search result dict with fields like
    *statute_title* or *chapter* that may inform prefill.
    """
    prefill: dict = {}
    year = extract_year(query)

    if route == "case_name":
        prefill["style_of_cause"] = query
    elif route == "legislation":
        prefill["title"] = (partial or {}).get("statute_title") or query
        if partial and partial.get("chapter"):
            prefill["chapter"] = partial["chapter"]
    elif route == "bill":
        prefill["bill_number"] = (partial or {}).get("bill_number") or query
    elif route == "citation_number":
        prefill["neutral_citation"] = query
    elif route == "concept":
        prefill["style_of_cause"] = query

    if year:
        prefill["year"] = year

    return prefill
