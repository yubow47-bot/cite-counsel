import os
import re
import json
import logging
import requests
from profiling import timing
from utils.json_util import parse_llm_json

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.getenv("LLM_DEFAULT_MODEL", "deepseek-v4-flash")


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


def _call_deepseek(messages: list, temperature: float = 0, model: str | None = None) -> str:
    """Internal: call DeepSeek API with messages, return response text."""
    api_key = _get_api_key()
    if not api_key:
        raise ValueError(
            "DEEPSEEK_API_KEY not configured. "
            "Set it in .env or config/settings.py"
        )
    actual_model = model or DEEPSEEK_MODEL

    with timing.measure("http.deepseek", model=actual_model):
        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": actual_model,
                "messages": messages,
                "temperature": temperature,
            },
            timeout=30,
        )
    response.raise_for_status()
    data = response.json()

    # ── Track token spend ──
    try:
        usage = data.get("usage", {})
        in_tokens = usage.get("prompt_tokens", 0)
        out_tokens = usage.get("completion_tokens", 0)
        if in_tokens or out_tokens:
            from core.spend_tracker import spend_tracker
            spend_tracker.record_cost("deepseek", actual_model, in_tokens, out_tokens)
    except Exception as e:
        logger.warning("DeepSeek spend tracking failed: %s", e)

    return data["choices"][0]["message"]["content"]


def ask_deepseek(prompt: str, model: str | None = None) -> str:
    """Single-turn prompt."""
    return _call_deepseek([{"role": "user", "content": prompt}], model=model)


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


def fetch_html(url: str, timeout: int = 15) -> str | None:
    """用 curl_cffi 伪装 Chrome TLS 指纹抓 HTML，失败返回 None。"""
    import curl_cffi.requests as cffi_requests
    try:
        r = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def extract_url(url: str) -> str | None:
    """抓取 + 解析。任一步失败返回 None，交给上层降级。"""
    html = fetch_html(url)
    if not html:
        return None
    import trafilatura
    return trafilatura.extract(html, include_comments=False)


def extract_from_url(url: str) -> dict:
    """Fetch a URL with curl_cffi and extract structured citation fields.

    Uses trafilatura's JSON output to get title, author, date, and sitename
    directly from the page metadata, without needing DeepSeek for extraction.
    Returns a dict with ``"error"`` key on failure so the caller can degrade
    to the manual scaffold.
    """
    import trafilatura

    html = fetch_html(url)
    if not html:
        return {"url": url, "error": "This website blocked automatic fetching (anti-scraping). Please fill in the citation fields manually."}

    try:
        result = trafilatura.extract(
            html,
            output_format="json",
            with_metadata=True,
            include_comments=False,
        )
        if result is None:
            return {"url": url, "error": "trafilatura could not extract content from this page"}
        meta = parse_llm_json(result)
    except Exception as e:
        print(f"[JSON解析] extract_from_url 失败: {e}  len={len(result) if result else 0}  result[:300]={result[:300]!r}")
        return {"url": url, "error": f"Content extraction failed: {e}"}

    # 从 hostname 推断来源名称（去掉 .com/.org 等后缀）
    hostname = meta.get("hostname", "") or ""
    sitename = re.sub(r"\.[a-z]{2,4}(?:\.[a-z]{2})?$", "", hostname).upper()
    if not sitename:
        sitename = hostname

    fields = {
        "url": url,
        "page_title": meta.get("title") or None,
        "author": meta.get("author") or None,
        "date": meta.get("date") or None,
        "newspaper": sitename or None,
        "hostname": hostname,
        "raw_text": meta.get("raw_text") or "",
        "style_of_cause": None,
        "neutral_citation": None,
        "statute_title": None,
        "jurisdiction": None,
        "year": (meta.get("date") or "")[:4] or None,
    }

    # 如果没有 newspaper，fallback 到 website 模式
    if not fields["newspaper"] and fields["page_title"]:
        fields["website"] = hostname

    return fields
