"""Vision extraction: structured citation fields from images, via OpenRouter (no local OCR).

Output matches the ``extract_from_url`` schema so callers treat both alike.
The API key is never echoed in logs or error messages.
"""

import base64
import logging
import os

from profiling import timing as prof
from utils.json_util import parse_llm_json

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """You are an OCR + structured-extraction assistant for legal citation processing.
Extract information from the provided image and return ONLY a JSON object with these fields:
{
  "page_title": "headline or title text visible in the image, or empty string",
  "author": "author name(s) if visible, or empty string",
  "date": "publication date in YYYY-MM-DD or YYYY format, or empty string",
  "newspaper": "site / publication name (e.g. The Globe and Mail), or empty string",
  "url": "URL if visible in the image, or empty string",
  "raw_text": "clean full-text transcription of all visible body text"
}
Rules:
- Output ONLY the JSON object. No markdown fences, no explanation, no prefix.
- If a field is not visible, use an empty string "".
- Transcribe raw_text verbatim and completely — do not paraphrase or summarise.
- Preserve paragraph breaks with single newlines in raw_text."""


def _read_image(image_path: str) -> tuple[str, str]:
    """Read an image file and return (base64_data, mime_type)."""
    with open(image_path, "rb") as f:
        raw = f.read()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    mime_type = mime.get(ext.lstrip("."), "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    return b64, mime_type


def _call_vision(image_paths: list[str]) -> dict | None:
    """Run the image extraction prompt through OpenRouter; None on any failure."""
    from llm_api.openrouter_api import check_budget, choice_text, post_chat_completion, track_usage

    if not check_budget():
        return None
    model = os.getenv("OPENROUTER_VISION_MODEL", "google/gemini-2.5-flash")
    content = [{"type": "text", "text": _EXTRACTION_PROMPT}]
    try:
        for path in image_paths:
            b64, mime = _read_image(path)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        with prof.measure("http.openrouter_vision", model=model):
            resp = post_chat_completion(body, read_timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = choice_text(data)
        track_usage(model, data)
        return parse_llm_json(text)
    except Exception:
        return None


def _align_fields(vision_result: dict) -> dict:
    """Map the vision result to the extract_from_url shared schema.

    Callers then treat vision and URL extraction results alike.
    """
    date_raw = (vision_result.get("date") or "").strip()
    newspaper_raw = (vision_result.get("newspaper") or "").strip()
    title_raw = (vision_result.get("page_title") or "").strip()
    raw_text = (vision_result.get("raw_text") or "").strip()

    # Keep the publication name exactly as extracted — it is a REAL name read
    # from the page (e.g. "The Globe and Mail"); upper-casing destroyed it.
    newspaper = newspaper_raw or None

    fields = {
        "url": (vision_result.get("url") or "").strip() or None,
        "page_title": title_raw or None,
        "author": (vision_result.get("author") or "").strip() or None,
        "date": date_raw or None,
        "newspaper": newspaper,
        "hostname": "",
        "style_of_cause": None,
        "neutral_citation": None,
        "statute_title": None,
        "jurisdiction": None,
        "year": date_raw[:4] if date_raw else None,
        "raw_text": raw_text[:3000] if raw_text else None,
    }

    # The vision schema has no hostname; absent values stay None/empty.
    return fields


def extract_from_image(image_path: str) -> dict:
    """Extract structured citation fields from a single image.

    Returns a dict matching the extract_from_url schema.
    On failure, returns {"error": "<message>"} so the caller degrades to scaffold.
    """
    try:
        result = _call_vision([image_path])
        if result is None:
            return {"error": "Vision API call or response parsing failed."}
        return _align_fields(result)
    except Exception:
        return {"error": "Vision extraction failed unexpectedly."}


def extract_from_images(image_paths: list[str]) -> dict:
    """Extract structured citation fields from multiple images (e.g. scanned pages).

    Useful for multi-page scans where each page is a separate image file.
    Returns the same schema as extract_from_image.
    """
    if not image_paths:
        return {"error": "No image paths provided."}

    try:
        result = _call_vision(image_paths)
        if result is None:
            return {"error": "Vision API call or response parsing failed."}
        return _align_fields(result)
    except Exception:
        return {"error": "Vision extraction failed unexpectedly."}
