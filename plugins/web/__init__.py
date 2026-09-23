"""Web search and page reading.

Which search service to use is the user's choice in the settings bar; with
none chosen, search reports that instead of picking one. Page reading goes
through the project's SSRF-guarded fetcher. Web content is information, not a
verified source: nothing here produces a citation.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field

from core.grounding import grounded_reply_guard
from harness.plugin import Plugin, Result, Setting, Tool

PROVIDERS = {"": "未选择", "duckduckgo_lite": "DuckDuckGo Lite（无需 key）"}


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def duckduckgo_lite(query: str, limit: int) -> list[dict]:
    from local_tools.utils import generic_session, request_with_retry
    response = request_with_retry(generic_session, "GET", "https://lite.duckduckgo.com/lite/",
                                  params={"q": query}, read_timeout=12,
                                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    response.raise_for_status()
    page = response.text
    links = re.findall(r"<a[^>]*href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", page, re.S)
    snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", page, re.S)
    results = []
    for index, (href, title) in enumerate(links):
        href = html.unescape(href)
        target = parse_qs(urlsplit(href).query).get("uddg", [href])[0]
        if not target.startswith(("http://", "https://")):
            continue
        results.append({"title": _clean(title), "url": target,
                        "snippet": _clean(snippets[index]) if index < len(snippets) else ""})
        if len(results) >= limit:
            break
    return results


SEARCHERS = {"duckduckgo_lite": duckduckgo_lite}


class SearchParams(BaseModel):
    query: str = Field(min_length=2, max_length=300)


def search(ctx, p: SearchParams) -> Result:
    provider = ctx.settings.get("provider") or ""
    searcher = SEARCHERS.get(provider)
    if searcher is None:
        return Result({"error": "No search service is selected. Tell the user to choose one in the settings "
                                "bar under this plugin."},
                      [{"type": "notice", "level": "warning",
                        "text": "网络搜索还没有选择搜索服务。请在设置栏的“网络搜索”插件里选择。"}], final=True)
    results = searcher(p.query, 6)
    return Result({"results": results},
                  [{"type": "web_results", "query": p.query, "results": results}])


class FetchParams(BaseModel):
    url: str = Field(min_length=8, max_length=2000, description="An http(s) URL")


def fetch(ctx, p: FetchParams) -> Result:
    from llm_api.deepseek_api import extract_from_url
    page = extract_from_url(p.url)
    if page.get("error") or not (page.get("raw_text") or "").strip():
        raise ValueError("the page could not be read (blocked, dynamic or empty)")
    text = page["raw_text"].strip()
    return Result({"url": p.url, "title": page.get("page_title") or "", "text": text[:5000],
                   "truncated": len(text) > 5000},
                  [{"type": "web_page", "url": p.url, "title": page.get("page_title") or p.url,
                    "chars": len(text)}])


PLUGIN = Plugin(
    name="web",
    title="网络搜索",
    description="Search the web and read public web pages, for background information that the legal "
                "databases do not cover.",
    instructions="Web pages are information, not verified sources. Say where something came from (the page), "
                 "and do not present web content as a citation.",
    tools=[Tool("search", "Search the web.", SearchParams, search),
           Tool("fetch", "Read the text of one public web page.", FetchParams, fetch)],
    settings=[Setting("provider", "搜索服务", "choice", tuple(PROVIDERS), "",
                      "选择后网络搜索才会发出请求。", labels=PROVIDERS)],
    ui=Path(__file__).parent,
    default_enabled=False,
    reply_guard=grounded_reply_guard,
)
