import os
import re
import json
import requests

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"


def _load_env():
    """Load .env file for API key."""
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
    """Get DeepSeek API key from .env via environment variable."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError(
            "DEEPSEEK_API_KEY not configured. "
            "Set it in the .env file: DEEPSEEK_API_KEY=sk-your-key-here"
        )
    return key


def _call_deepseek(messages: list, temperature: float = 0) -> str:
    """Internal: call DeepSeek API with messages, return response text."""
    api_key = _get_api_key()
    if not api_key:
        raise ValueError(
            "DEEPSEEK_API_KEY not configured. "
            "Set it in .env or config/settings.py"
        )

    response = requests.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def ask_deepseek(prompt: str) -> str:
    """Single-turn prompt, replaces ask_ollama."""
    return _call_deepseek([{"role": "user", "content": prompt}])


def chat_deepseek(messages: list) -> str:
    """Multi-turn chat, replaces chat_ollama."""
    return _call_deepseek(messages, temperature=0.7)


def fetch_url_content(url: str) -> str:
    """Fetch and extract plain text from a URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        text = re.sub(r"<[^>]+>", "", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]
    except requests.exceptions.ConnectTimeout:
        return "Failed to fetch URL: Connection timed out"
    except requests.exceptions.ConnectionError:
        return "Failed to fetch URL: Connection refused"
    except requests.exceptions.ReadTimeout:
        return "Failed to fetch URL: Server took too long to respond"
    except Exception as e:
        return f"Failed to fetch URL: {e}"


def extract_from_url(url: str) -> dict:
    """Fetch a URL and use DeepSeek to extract citation fields (replaces Kimi version)."""
    page_content = fetch_url_content(url)
    if page_content.startswith("Failed to fetch URL"):
        return {"url": url, "error": page_content}

    prompt = f"""Extract citation fields from the following content.
Return JSON only, no explanation. Use null for missing fields.

{{
  "style_of_cause": "parties name if case law",
  "neutral_citation": "neutral citation if case law",
  "reporter": "reporter if case law",
  "statute_title": "statute title if legislation",
  "jurisdiction": "jurisdiction if legislation",
  "chapter": "chapter number if legislation",
  "degree": "degree type if thesis",
  "institution": "university if thesis",
  "unpublished": "true if unpublished thesis, otherwise null",
  "video": "true if video, otherwise null",
  "timestamp": "timestamp pinpoint if video, otherwise null",
  "url": "{url}",
  "platform": "social media platform name if social media post",
  "post": "first sentence of post if social media",
  "newspaper": "newspaper name if news article",
  "page_title": "page or article title if website",
  "author": "author name(s), null if none",
  "journal": "journal name if academic article",
  "volume": "volume number if journal article",
  "publisher": "publisher name if book",
  "place_of_publication": "city of publication if book",
  "issuing_body": "government body name if government document",
  "government_jurisdiction": "country or province if government document",
  "year": "publication year",
  "date": "full publication date"
}}

Content:
{page_content}"""

    try:
        print("  调用 DeepSeek API 中...", flush=True)
        content = ask_deepseek(prompt)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"url": url, "page_title": content}
    except Exception as e:
        return {"url": url, "error": f"DeepSeek API error: {e}"}
