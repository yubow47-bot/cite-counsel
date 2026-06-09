import json
import re

from llm_api.deepseek_api import ask_deepseek
from local_tools.a2aj_api import fetch_by_citation, search_cases_multi, _map_fields, _extract_year


def classify_and_normalize(query: str) -> dict:
    """判断输入类型并标准化。"""
    prompt = f"""你是加拿大法律引用专家。分析以下用户输入，完成两件事：
1. 判断输入类型（只能是以下四种之一）：
   - citation_number：已知的引用号，如 "2022 SCC 39"、"[1999] 1 SCR 688"、"RSC 1985, c C-46"
   - case_name：案件名，如 "R v Gladue"、"R. v. Sharma"、"Regina v Jordan"
   - legislation：法条名或法条缩写，如 "Criminal Code"、"CCC"、"Charter"、"CCC s.718.2(e)"
   - concept：法律概念或原则，如 "gladue principle"、"right to housing"、"duty to consult"

2. 标准化输入：
   - 案件名：去掉句号（R. v. → R v），Regina/The Queen → R，去掉末尾的 "case"
   - 法条缩写：展开成完整引用（CCC → Criminal Code, RSC 1985, c C-46）
   - 法条+条款混合：展开法条名，保留条款（CCC s.718.2(e) → Criminal Code, RSC 1985, c C-46, s 718.2(e)）
   - 条款格式：s.718 → s 718（去掉句号）
   - 法语输入同样处理（R. c. → R c）

只返回 JSON，不要任何解释：
{{"type": "类型", "normalized": "标准化后的输入", "original": "原始输入"}}

用户输入：{query}"""

    try:
        content = ask_deepseek(prompt)
        result = json.loads(content)
        if result.get("type") in ("citation_number", "case_name", "legislation", "concept"):
            return result
    except Exception:
        pass
    return {"type": "case_name", "normalized": query, "original": query}



def expand_concept(query: str) -> list:
    """展开法律概念：源头案件 + 法条 + 后续案件。"""
    prompt = f"""你是加拿大法律专家。用户查询的法律概念是："{query}"

请给出以下内容（只给你确定知道的，不确定的不要编造）：
1. 确立该原则的源头案件（完整 citation）
2. 相关法条（完整 citation，包括具体条款）
3. 最重要的2-3个后续案件（完整 citation）

只返回 JSON 数组，格式：
[
  {{"type": "case", "citation": "引用号", "name": "案件名", "role": "源头案件"}},
  {{"type": "legislation", "citation": "引用号", "name": "法条名", "role": "相关法条"}},
  {{"type": "case", "citation": "引用号", "name": "案件名", "role": "重要后续案件"}}
]
不要任何解释。"""

    try:
        content = ask_deepseek(prompt)
        items = json.loads(content)
        if not isinstance(items, list):
            return []
    except Exception:
        return []

    results = []
    for item in items:
        citation = item.get("citation", "")
        if not citation:
            continue
        # 尝试验证
        try:
            verified = fetch_by_citation(citation)
            has_content = "style_of_cause" in verified or "statute_title" in verified
        except Exception:
            verified = {}
            has_content = False

        entry = {
            "name": item.get("name", citation),
            "neutral_citation": citation,
            "role": item.get("role", ""),
            "verified": has_content,
        }
        if has_content:
            entry.update(verified)
        else:
            entry["warning"] = "⚠️ 未能通过 A2AJ 验证，建议在 CanLII 手动确认"
        results.append(entry)

    return results


def search_citation(query: str) -> list:
    """主入口：分类 → 标准化 → 搜索/验证。"""
    classified = classify_and_normalize(query)
    print(f"[DEBUG] 分类结果: {classified}")
    input_type = classified["type"]
    normalized = classified["normalized"]

    # 1. citation_number：按引用号精确查
    if input_type == "citation_number":
        result = fetch_by_citation(normalized)
        if "error" not in result and "raw_input" not in result:
            result["verified"] = True
            return [result]
        return [{
            "name": normalized,
            "verified": False,
            "warning": "⚠️ 未能通过 A2AJ 验证，建议在 CanLII 手动确认",
        }]

    # 2. case_name：按名称搜索 + 可选年份过滤
    elif input_type == "case_name":
        # 从原始输入提取年份，与 DeepSeek 标准化互不干扰
        _, year = _extract_year(classified["original"])
        start_date = f"{year}-01-01" if year else None
        end_date = f"{year}-12-31" if year else None
        if year:
            print(f"[DEBUG] 从原始输入提取到年份: {year} → {start_date} ~ {end_date}")

        # 提取核心关键词（去掉 R v / R c / Regina v 等前缀）
        keyword = re.sub(
            r"^(?:R\s+v|R\s+c|Regina\s+v|The\s+Queen\s+v)\s+",
            "",
            normalized,
            flags=re.IGNORECASE
        ).strip()

        title_matches = []
        fulltext_pool = []
        MAX_BATCHES = 3

        for batch in range(MAX_BATCHES):
            batch_results = search_cases_multi(
                normalized, size=40, offset=batch * 40,
                start_date=start_date, end_date=end_date,
            )
            if not batch_results:
                break

            if batch == 0:
                fulltext_pool = batch_results

            for r in batch_results:
                name = r.get("name_en", "")
                if keyword.lower() in name.lower() and r not in title_matches:
                    title_matches.append(r)
                if len(title_matches) >= 2:
                    break

            if len(title_matches) >= 2:
                break

        seen = set()
        final = []
        for r in title_matches + fulltext_pool:
            key = r.get("citation_en") or r.get("name_en")
            if key and key not in seen:
                seen.add(key)
                final.append(r)
            if len(final) >= 5:
                break

        if not final:
            return []
        return [dict(_map_fields(r), verified=True) for r in final]

    # 3. legislation：A2AJ /fetch (doc_type=laws) 验证
    elif input_type == "legislation":
        import requests

        # 从标准化文本中提取基础引用号
        # 匹配 SC/RSC/SOR 等编号（去掉法条名和条款部分）
        cit_match = re.search(
            r"(?:RSC|SC|SOR|RRO|O\sReg|BC\sReg|RLRQ)\s[^,]+(?:,\s*c\s[^,]+)?",
            normalized
        )
        base_citation = cit_match.group(0).strip() if cit_match else normalized
        verified = False
        try:
            resp = requests.get(
                "https://api.a2aj.ca/fetch",
                params={"citation": base_citation, "doc_type": "laws"},
                timeout=15
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            verified = len(results) > 0
        except Exception:
            verified = False

        return [{
            "statute_title": normalized,
            "verified": verified,
            "warning": "" if verified else "⚠️ 未能通过 A2AJ 验证，建议在 CanLII 手动确认",
        }]

    # 4. concept：概念展开
    elif input_type == "concept":
        return expand_concept(normalized)

    return []
