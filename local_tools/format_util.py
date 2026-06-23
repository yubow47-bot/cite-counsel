"""Shared formatting helpers for deterministic citation builders (no LLM)."""


def _wrap_italic(s: str) -> str:
    """Wrap s in *...* if not already wrapped. Only manage outer wrapping;
    never touch formatting markers inside the value."""
    if not s:
        return s
    s = s.strip()
    if s.startswith("*") and s.endswith("*"):
        return s
    return f"*{s}*"
