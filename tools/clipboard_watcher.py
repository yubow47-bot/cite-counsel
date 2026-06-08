import json
import os

from llm_api.local_ollama import ask_ollama

SESSION_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "current_session.json")


def _load_citations() -> list[dict]:
    """读取当前 session 的引用列表。"""
    if not os.path.exists(SESSION_PATH):
        return []
    try:
        with open(SESSION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("citations", [])
    except Exception:
        return []


def _build_prompt(citations: list[dict], clip_text: str) -> str:
    """构建 Ollama 判断 prompt。"""
    lines = []
    for c in citations:
        lines.append(f'{c["num"]}. {c["full"]} (简称: {c["short"]})')
    citation_list = "\n".join(lines)
    return f"""
引用列表：
{citation_list}

选中文字："{clip_text}"

判断选中文字在讨论上面列表中的哪一条，只输出对应编号（数字）。
如果对应不上任何一条，只输出 0。
只输出数字，不要解释，不要加其他文字。
"""


def _format_result(match_num: int, citations: list[dict]) -> str | None:
    """将匹配的编号转为规范的引用格式。"""
    if match_num <= 0 or not citations:
        return None

    last_num = citations[-1]["num"]
    if match_num == last_num:
        return "Ibid."

    for c in citations:
        if c["num"] == match_num:
            return f'{c["short"]}, supra note {match_num}.'
    return None


def match_citation(clip_text: str) -> str | None:
    """判断一段文字匹配引用库中的哪一条引用。

    返回:
        - "Ibid." —— 匹配最后一条引用
        - "简称, supra note X." —— 匹配更早的引用
        - None —— 无法匹配
    """
    citations = _load_citations()
    if not citations:
        return None

    prompt = _build_prompt(citations, clip_text)
    try:
        response = ask_ollama(prompt)
    except Exception:
        return None

    try:
        match_num = int(response.strip())
    except (ValueError, TypeError):
        return None

    return _format_result(match_num, citations)
