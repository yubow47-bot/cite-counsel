"""Conservative text matching for provenance, never work identity verification."""
import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def find_text(text: str, value: str) -> int:
    needle = normalize(value)
    if not needle:
        return -1
    pattern = (r"(?<!\w)" if needle[0].isalnum() or needle[0] == "_" else "")
    pattern += re.escape(needle)
    pattern += r"(?!\w)" if needle[-1].isalnum() or needle[-1] == "_" else ""
    match = re.search(pattern, normalize(text))
    return match.start() if match else -1
