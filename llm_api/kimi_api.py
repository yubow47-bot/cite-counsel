"""Kimi (Moonshot) vision API: extract structured fields from images (no LLM call).

Mirrors deepseek_api.py's env-loading pattern and extract_from_url's output schema,
so downstream classify_document_type + format_citation need zero changes.

Never echoes the API key in logs or error messages.
"""

import base64
import os
import re
import requests
from local_tools.utils import kimi_session
from profiling import timing as prof
from utils.json_util import parse_llm_json

KIMI_BASE_URL = "https://api.moonshot.cn/v1"
KIMI_VISION_MODEL = os.getenv("KIMI_VISION_MODEL", "moonshot-v1-8k-vision-preview")

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


def _load_env():
    """Load .env file for API key (mirrors deepseek_api.py)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_env()


def _get_api_key() -> str:
    """Get Kimi API key from environment."""
    key = os.environ.get("KIMI_API_KEY", "")
    if not key:
        raise ValueError("KIMI_API_KEY not configured. Set it in the .env file.")
    return key


def _image_to_data_uri(image_path: str) -> str:
    """Read an image file and return a data URI string."""
    with open(image_path, "rb") as f:
        raw = f.read()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    mime_type = mime.get(ext.lstrip("."), "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _build_content(image_paths: list[str]) -> list[dict]:
    """Build the messages content array: text instruction + one or more image blocks."""
    parts = [{"type": "text", "text": _EXTRACTION_PROMPT}]
    for path in image_paths:
        data_uri = _image_to_data_uri(path)
        parts.append({"type": "image_url", "image_url": {"url": data_uri}})
    return parts


def _call_kimi_vision(image_paths: list[str]) -> dict:
    """Call the Kimi vision API and return the parsed JSON dict.

    Returns None if the API call or JSON parsing fails.
    """
    api_key = _get_api_key()
    url = f"{KIMI_BASE_URL}/chat/completions"
    messages = [{"role": "user", "content": _build_content(image_paths)}]

    try:
        with prof.measure("http.kimi", model=KIMI_VISION_MODEL):
            resp = kimi_session.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": KIMI_VISION_MODEL,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": 4096,
                },
                timeout=30,
            )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

    try:
        return parse_llm_json(text)
    except Exception:
        return None


def _align_fields(kimi_result: dict, image_path: str) -> dict:
    """Map the Kimi vision result to the extract_from_url shared schema.

    This ensures zero changes to classify_document_type and format_citation.
    """
    date_raw = (kimi_result.get("date") or "").strip()
    newspaper_raw = (kimi_result.get("newspaper") or "").strip()
    title_raw = (kimi_result.get("page_title") or "").strip()
    raw_text = (kimi_result.get("raw_text") or "").strip()

    # Normalise newspaper to uppercase (mirroring extract_from_url's sitename logic)
    newspaper = newspaper_raw.upper() if newspaper_raw else None

    fields = {
        "url": image_path,
        "page_title": title_raw or None,
        "author": (kimi_result.get("author") or "").strip() or None,
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

    # Fallback to website mode when no newspaper but has page_title
    if not fields["newspaper"] and fields["page_title"]:
        fields["website"] = ""

    return fields


def extract_from_image(image_path: str) -> dict:
    """Extract structured citation fields from a single image.

    Returns a dict matching the extract_from_url schema.
    On failure, returns {"error": "<message>"} so the caller degrades to scaffold.
    """
    try:
        result = _call_kimi_vision([image_path])
        if result is None:
            return {"error": "Kimi vision API call or response parsing failed."}
        return _align_fields(result, image_path)
    except Exception:
        return {"error": "Kimi vision extraction failed unexpectedly."}


def extract_from_images(image_paths: list[str]) -> dict:
    """Extract structured citation fields from multiple images (e.g. scanned pages).

    Useful for multi-page scans where each page is a separate image file.
    Returns the same schema as extract_from_image.
    """
    if not image_paths:
        return {"error": "No image paths provided."}

    try:
        result = _call_kimi_vision(image_paths)
        if result is None:
            return {"error": "Kimi vision API call or response parsing failed."}
        return _align_fields(result, image_paths[0])
    except Exception:
        return {"error": "Kimi vision extraction failed unexpectedly."}
