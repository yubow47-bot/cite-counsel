"""Web search and page reading.

The search service is the user's choice in the settings bar: Exa (an API
key they fill in themselves -- in the settings bar, or as ``EXA_API_KEY``
in the project's ``.env``) or the keyless DuckDuckGo Lite. With none
chosen, search reports that instead of picking one; a searcher that is
blocked says so instead of quietly returning nothing -- and two empty
searches in a row tell the model to stop retrying and read known pages
directly. Page reading goes through the project's SSRF-guarded fetcher
(deterministic extraction, no model in between). Web content is
information, not a verified source: nothing here produces a citation, but
a read page is stored with the date and author its metadata carries.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field

from harness.plugin import Plugin, Result, Setting, Tool

PROVIDERS = {"": "未选择", "exa": "Exa（需 API Key）", "duckduckgo_lite": "DuckDuckGo Lite（无需 key，常被拦）"}
_EMPTY_STREAK_WARN = 2
_EMPTY_STREAK_NOTE = ("搜索已连续 {n} 次无结果：搜索服务可能被拦截，或对这个话题没有覆盖。"
                      "改用 fetch 直接读权威站点的已知版面（如 cbc.ca/news），或告诉用户搜索暂不可用。")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def duckduckgo_lite(query: str, limit: int) -> list[dict]:
    from local_tools.utils import generic_session, request_with_retry
    response = request_with_retry(generic_session, "GET", "https://lite.duckduckgo.com/lite/",
                                  params={"q": query}, read_timeout=12,
                                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    response.raise_for_status()
    page = response.text
    # An anomaly/challenge page (HTTP 202, canonical pointed at the homepage)
    # parses as zero results; say what happened instead of reporting a clean miss.
    if response.status_code == 202 or '<link rel="canonical" href="https://duckduckgo.com/">' in page:
        raise ValueError("DuckDuckGo Lite 返回了人机校验页，搜索不可用；改用 fetch，或让用户换 Exa。")
    links = re.findall(r"<a[^>]*href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", page, re.S)
    snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", page, re.S)
    results = []
    for index, (href, title) in enumerate(links):
        href = html.unescape(href)
        target = parse_qs(urlsplit(href).query).get("uddg", [href])[0]
        if not target.startswith(("http://", "https://")):
            continue
        results.append({"title": _clean(title), "url": target,
                        "snippet": _clean(snippets[index]) if index < len(snippets) else "",
                        "date": "", "author": ""})
        if len(results) >= limit:
            break
    return results


def exa_search(query: str, limit: int, api_key: str, latest_days: int = 0) -> list[dict]:
    """Exa /search: neural + keyword hybrid, with each result's publication date.

    ``latest_days`` filters to results published in the last N days, so a
    "latest news" request does not surface a years-old story undated.
    """
    from datetime import date, timedelta

    from local_tools.utils import generic_session, request_with_retry
    body: dict = {"query": query, "numResults": limit, "type": "auto",
                  "contents": {"text": {"maxCharacters": 500}}}
    if latest_days:
        body["startPublishedDate"] = (date.today() - timedelta(days=latest_days)).isoformat()
    response = request_with_retry(
        generic_session, "POST", "https://api.exa.ai/search",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json=body, read_timeout=15, retries=1)
    response.raise_for_status()
    results = []
    for item in (response.json() or {}).get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        results.append({"title": str(item.get("title") or "").strip(), "url": url,
                        "snippet": re.sub(r"\s+", " ", str(item.get("text") or "")).strip()[:300],
                        "date": str(item.get("publishedDate") or "").strip()[:10],
                        "author": str(item.get("author") or "").strip()})
    return results


class SearchParams(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    latest_days: int = Field(default=0, ge=0, le=365,
                             description="Only results published in the last N days (0 = no filter); "
                                         "use it when the user asks for the latest news")


def search(ctx, p: SearchParams) -> Result:
    provider = ctx.settings.get("provider") or ""
    if provider not in {name for name in PROVIDERS if name}:
        return Result({"error": "No search service is selected. Tell the user to choose one in the settings "
                                "bar under this plugin."},
                      [{"type": "notice", "level": "warning",
                        "text": "网络搜索还没有选择搜索服务。请在设置栏的“网络搜索”插件里选择。"}], final=True)
    try:
        if provider == "exa":
            # The settings-bar value wins; the project .env (EXA_API_KEY=...)
            # is the fallback so the key can live with the other secrets.
            api_key = str(ctx.settings.get("exa_api_key") or "").strip() or os.getenv("EXA_API_KEY", "").strip()
            if not api_key:
                return Result({"error": "Exa is selected but no API key is set. Tell the user to paste their "
                                        "Exa API key in the settings bar under this plugin, or set EXA_API_KEY "
                                        "in the project's .env."},
                              [{"type": "notice", "level": "warning",
                                "text": "选择了 Exa 但还没有 API Key。请在 .env 里写 EXA_API_KEY=你的key，"
                                        "或在设置栏的“网络搜索”插件里粘贴。"}], final=True)
            ctx.session.web_search_used = True   # a real attempt: even a failed one counts (record__new)
            results = exa_search(p.query, 6, api_key, p.latest_days)
        else:
            ctx.session.web_search_used = True   # a real attempt: even a blocked one counts (record__new)
            results = duckduckgo_lite(p.query, 6)
    except ValueError:
        raise
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = {401: "API Key 无效或无权限", 402: "账户额度不足", 403: "API Key 无效或无权限",
                  429: "请求太频或超出配额，稍后再试"}.get(status, f"搜索服务请求失败（{type(exc).__name__}）")
        raise ValueError(detail) from exc
    streak = ctx.state.get("empty_search_streak", 0)
    note = ""
    if results:
        ctx.state["empty_search_streak"] = 0
    else:
        streak += 1
        ctx.state["empty_search_streak"] = streak
        if streak >= _EMPTY_STREAK_WARN:
            note = _EMPTY_STREAK_NOTE.format(n=streak)
    return Result({"results": results, "note": note},
                  [{"type": "web_results", "query": p.query, "results": results,
                    **({"note": note} if note else {})}])


class FetchParams(BaseModel):
    url: str = Field(min_length=8, max_length=2000, description="An http(s) URL")


def fetch(ctx, p: FetchParams) -> Result:
    from core.tool_contracts import Field, Record
    from llm_api.deepseek_api import extract_from_url
    page = extract_from_url(p.url)
    if page.get("error") or not (page.get("raw_text") or "").strip():
        raise ValueError("the page could not be read (blocked, dynamic or empty)")
    text = page["raw_text"].strip()
    site = str(page.get("site_name") or "").strip()
    title = str(page.get("page_title") or p.url).strip()
    if site and title.endswith(f" | {site}"):
        title = title[: -len(f" | {site}")].strip()      # the page chrome, not the headline
    fields = {"title": Field(title, "extracted"), "url": Field(p.url, "extracted"), "text": Field(text, "extracted")}
    for name, value in (("date", page.get("date")), ("author", page.get("author")), ("site_name", site)):
        value = str(value or "").strip()
        if value:
            fields[name] = Field(value, "extracted")
    record = Record("website", fields, "web", p.url)
    ref = ctx.save(record)
    content = {"url": p.url, "title": title, "text": text[:5000], "truncated": len(text) > 5000,
               "record": {"ref": ref}}
    for name in ("date", "author"):
        if name in fields:
            content[name] = fields[name].value
    block = {"type": "web_page", "url": p.url, "title": title, "chars": len(text)}
    if "date" in fields:
        block["date"] = fields["date"].value
    return Result(content, [block])


PLUGIN = Plugin(
    name="web",
    title="网络搜索",
    description="Search the web and read public web pages, for background information that the legal "
                "databases do not cover.",
    instructions="Web pages are information, not verified sources. A page you read is stored as an extracted "
                 "record with the date and author its metadata carries; say where something came from (the "
                 "page, with its date) and do not present web content as a citation. For news the user calls "
                 "latest, pass latest_days and quote each result's date -- an undated hit is not a fresh one.",
    tools=[Tool("search", "Search the web (Exa; supports a latest-N-days filter).", SearchParams, search),
           Tool("fetch", "Read the text of one public web page.", FetchParams, fetch)],
    settings=[Setting("provider", "搜索服务", "choice", tuple(PROVIDERS), "",
                      "选择后网络搜索才会发出请求。", labels=PROVIDERS),
              Setting("exa_api_key", "Exa API Key", "secret", default="",
                      help="在 exa.ai 申请；填在这里，或写进 .env 的 EXA_API_KEY（设置栏的值优先）。")],
    ui=Path(__file__).parent,
    category="extract",
    fact_patterns=(r"\bhttps?://\S+",),
    default_enabled=False,
)
