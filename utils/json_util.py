"""JSON parsing helpers for LLM output and other semi-structured text.

``parse_llm_json`` tolerates markdown fences, explanatory text, and trailing
data — all common LLM output quirks that break bare ``json.loads``.
"""
import json


def parse_llm_json(text: str):
    """Parse the first complete JSON value from *text*, ignoring trailing noise.

    Uses ``json.JSONDecoder.raw_decode`` under the hood — it consumes only
    one value and returns ``(obj, end_pos)``, so leftover text is harmless.

    Raises ``ValueError`` when no JSON value is found.
    """
    start = next((i for i, c in enumerate(text) if c in "{["), None)
    if start is None:
        raise ValueError(
            f"No JSON object/array found in response: {text[:200]!r}"
        )
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj
