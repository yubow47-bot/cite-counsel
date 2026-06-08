import os
import re
import requests
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


_load_env()

KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
KIMI_API_URL = "https://api.moonshot.cn/v1/chat/completions"
KIMI_MODEL = "moonshot-v1-8k"


def _make_session(timeout: int = 8) -> requests.Session:
    session = requests.Session()
    retry = Retry(total=0)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.timeout = timeout
    # 从环境变量读取代理（Windows 系统代理或手动设置）
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if os.environ.get(var):
            session.proxies.update({var.lower().split("_")[0]: os.environ[var]})
            break
    return session


def fetch_url_content(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        session = _make_session(timeout=8)
        print("  抓取网页中...", flush=True)
        resp = session.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        text = re.sub(r"<[^>]+>", "", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]
    except requests.exceptions.ConnectTimeout:
        return "Failed to fetch URL: Connection timed out (site may be unreachable from your network)"
    except requests.exceptions.ConnectionError:
        return "Failed to fetch URL: Connection refused — the site may be blocked in your region"
    except requests.exceptions.ReadTimeout:
        return "Failed to fetch URL: Server took too long to respond"
    except Exception as e:
        return f"Failed to fetch URL: {e}"


def extract_from_url(url: str) -> dict:
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
        print("  调用 Kimi API 中...", flush=True)
        session = _make_session(timeout=30)
        response = session.post(
            KIMI_API_URL,
            headers={
                "Authorization": f"Bearer {KIMI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": KIMI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except Exception:
            return {"url": url, "page_title": content}
    except requests.exceptions.SSLError as e:
        return {"url": url, "error": f"SSL connection failed: {e}"}
    except requests.exceptions.ConnectionError as e:
        return {"url": url, "error": f"Connection failed: {e}"}
    except requests.exceptions.Timeout:
        return {"url": url, "error": "Request timed out"}
    except Exception as e:
        return {"url": url, "error": f"Kimi API error: {e}"}