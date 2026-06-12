import os
import re
import json
import requests
from profiling import timing

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

    with timing.measure("http.deepseek", model=DEEPSEEK_MODEL):
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
    """Fetch a URL with trafilatura and extract structured citation fields.

    Uses trafilatura's JSON output to get title, author, date, and sitename
    directly from the page metadata, without needing DeepSeek for extraction.
    """
    import trafilatura

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return {"url": url, "error": "trafilatura 未能下载该 URL"}
        result = trafilatura.extract(
            downloaded,
            output_format="json",
            with_metadata=True,
            include_comments=False,
        )
        if result is None:
            return {"url": url, "error": "trafilatura 未能从页面提取到内容"}

        meta = json.loads(result)
    except Exception as e:
        return {"url": url, "error": f"trafilatura 提取失败: {e}"}

    # 从 hostname 推断来源名称（去掉 .com/.org 等后缀）
    hostname = meta.get("hostname", "") or ""
    sitename = re.sub(r"\.[a-z]{2,4}(?:\.[a-z]{2})?$", "", hostname).upper()
    if not sitename:
        sitename = hostname

    # 映射 trafilatura 字段 → McGill 引擎字段
    fields = {
        "url": url,
        "page_title": meta.get("title") or None,
        "author": meta.get("author") or None,
        "date": meta.get("date") or None,
        "newspaper": sitename or None,
        "hostname": hostname,
        # 留空让 detect_type 自己判断类型
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
