import requests

A2AJ_BASE = "https://api.a2aj.ca"


def fetch_by_citation(citation: str) -> dict:
    """按 citation 直接查询，返回结构化字段。"""
    try:
        response = requests.get(
            f"{A2AJ_BASE}/fetch",
            params={"citation": citation, "doc_type": "cases"},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if not results:
            # 尝试 legislation
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
def search_cases_multi(query: str, size: int = 5) -> list:
    """模糊搜索，返回多条结果供用户选择。"""
    try:
        response = requests.get(
            f"{A2AJ_BASE}/search",
            params={"query": query, "doc_type": "cases", "size": size},
            timeout=15
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return results
    except requests.exceptions.RequestException as e:
        return []