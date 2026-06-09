import re

import requests

A2AJ_BASE = "https://api.a2aj.ca"


def fetch_by_citation(citation: str, doc_type: str = "cases") -> dict:
    """按 citation 直接查询，返回结构化字段。

    Args:
        citation: 引用号
        doc_type: "cases" 或 "legislation"。默认为 "cases"，
                  设为 "legislation" 时只查法条不做 fallback。
    """
    try:
        response = requests.get(
            f"{A2AJ_BASE}/fetch",
            params={"citation": citation, "doc_type": doc_type},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if not results and doc_type == "cases":
            # cases 查不到时尝试 legislation
            try:
                response = requests.get(
                    f"{A2AJ_BASE}/fetch",
                    params={"citation": citation, "doc_type": "legislation"},
                    timeout=15
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            except requests.exceptions.RequestException:
                results = []
        if not results:
            return {"raw_input": citation}
        return _map_fields(results[0])
    except requests.exceptions.RequestException as e:
        return {"raw_input": citation, "error": f"A2AJ fetch failed: {e}"}


def _map_fields(result: dict) -> dict:
    """将 A2AJ 返回字段映射到 detect_type 能识别的格式。"""
    citation = result.get("citation_en", "")
    name = result.get("name_en", "")
    date = result.get("document_date_en", "")
    year = date[:4] if date else ""

    # 判断是判例还是法规
    dataset = result.get("dataset", "")
    is_legislation = "LEGISLATION" in dataset.upper()

    if is_legislation:
        return {
            "statute_title": name,
            "neutral_citation": citation,
            "jurisdiction": _extract_jurisdiction(dataset),
            "year": year,
            "url": result.get("url_en", ""),
        }
    else:
        reporter = result.get("citation2_en", "") or citation
        return {
            "style_of_cause": name,
            "neutral_citation": citation,
            "reporter": reporter,
            "year": year,
            "date": date,
            "url": result.get("url_en", ""),
        }


def _extract_jurisdiction(dataset: str) -> str:
    """从 dataset 字段提取管辖区。"""
    mapping = {
        "FED": "Canada",
        "ON": "Ontario",
        "BC": "British Columbia",
    }
    for key, value in mapping.items():
        if key in dataset.upper():
            return value
    return ""


def _dedup_key(result: dict) -> str:
    """生成去重用的唯一键。"""
    return result.get("citation_en", "") or result.get("name_en", "") or result.get("url_en", "")


def _extract_year(text: str) -> tuple[str, str | None]:
    """从文本中提取4位年份，返回(清洗后的文本, 年份或None)。"""
    m = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if m:
        year = m.group(1)
        cleaned = re.sub(r'\b' + year + r'\b', '', text).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned, year
    return text, None


def search_cases_multi(query: str, size: int = 40, offset: int = 0,
                       search_type: str = "name",
                       start_date: str | None = None,
                       end_date: str | None = None) -> list:
    """双轨搜索：先用 /fetch 精确匹配，再用 /search 补充。

    Args:
        query: 案件名（会自动提取年份并清理输入）
        size: 返回条数
        offset: 偏移量
        search_type: "name" 按标题或 "full_text" 按全文
        start_date: 开始日期 YYYY-MM-DD（不传则从 query 中提取年份）
        end_date: 结束日期 YYYY-MM-DD（不传则从 query 中提取年份）
    """
    seen = set()
    merged = []

    # 从 query 中提取年份并清洗
    clean_query, year = _extract_year(query)
    if year and not start_date and not end_date:
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

    # 第一轨：/fetch 精确匹配
    try:
        resp = requests.get(
            f"{A2AJ_BASE}/fetch",
            params={"citation": clean_query, "doc_type": "cases"},
            timeout=15
        )
        resp.raise_for_status()
        fetch_results = resp.json().get("results", [])
    except requests.exceptions.RequestException:
        fetch_results = []

    for r in fetch_results:
        name = (r.get("name_en") or "").lower()
        citation_en = (r.get("citation_en") or "").lower()
        if clean_query.lower() in name or clean_query.lower() in citation_en:
            key = _dedup_key(r)
            if key and key not in seen:
                seen.add(key)
                merged.append(r)

    # 第二轨：/search（search_type=name 按案件名搜索）
    params = {
        "query": clean_query,
        "doc_type": "cases",
        "size": size,
        "search_type": search_type,
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(
            f"{A2AJ_BASE}/search",
            params=params,
            timeout=15
        )
        resp.raise_for_status()
        search_results = resp.json().get("results", [])
    except requests.exceptions.RequestException:
        search_results = []

    for r in search_results:
        key = _dedup_key(r)
        if key and key not in seen:
            seen.add(key)
            merged.append(r)

    return merged[:size]