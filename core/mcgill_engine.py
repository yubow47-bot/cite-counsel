import json
import time

from llm_api.deepseek_api import ask_deepseek
from local_tools import timing_util as timing

import os
RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcgill_rules.json")

# 调试缓存：最近一次 format_citation 的 prompt 和原始返回
_last_prompt = None
_last_raw_response = None


def get_last_debug() -> dict:
    """返回最近一次 format_citation 的调试信息。"""
    return {
        "prompt": _last_prompt,
        "raw_response": _last_raw_response,
    }


def detect_type(extracted_fields: dict) -> str:
    """根据提取字段自动判断 McGill 引用类型。"""
    keys = {k.lower().replace(" ", "_") for k, v in extracted_fields.items() if v is not None}

    def has(*candidates):
        return any(c in keys for c in candidates)

    # 1. Jurisprudence
    if has("style_of_cause", "neutral_citation", "reporter"):
        return "jurisprudence"

    # 2. Legislation (statute)
    if has("statute_title", "title") and has("jurisdiction", "chapter"):
        return "legislation"

    # 3. Theses – 特征最明显，优先判断
    if has("degree", "institution", "unpublished"):
        return "secondary_sources.theses"

    # 4. Online videos
    if has("video_indicator", "video", "timestamp") and has("url"):
        return "secondary_sources.online_videos"

    # 5. Social media
    if has("platform_url", "platform", "post_content", "social_media", "post"):
        return "secondary_sources.social_media"

    # 6. News sources
    if has("newspaper_name", "newspaper"):
        return "secondary_sources.news_sources"

    # 7. Websites
    if has("url", "website") and has("page_title", "title"):
        return "secondary_sources.websites"

    # 8. Journal articles
    if has("author") and has("journal_name", "journal") and has("volume"):
        return "secondary_sources.journal_articles"

    # 9. Books
    if has("author") and has("publisher", "place_of_publication"):
        return "secondary_sources.books"

    # 10. Government documents
    if has("issuing_body", "government_jurisdiction"):
        return "government_docs"

    # 11. Legislation fallback (只有 title + jurisdiction/chapter 之一)
    if has("statute_title", "title") and (has("jurisdiction") or has("chapter")):
        return "legislation"

    return "general_rules"


def get_rules(detected_type: str) -> dict:
    """从 mcgill_rules.json 读取并返回与类型相关的规则片段。"""
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        rules_db = json.load(f)

    if detected_type.startswith("secondary_sources."):
        subtype = detected_type.split(".", 1)[1]
        category_data = rules_db.get("secondary_sources", {})
        all_topics = category_data.get("topics", [])

        topic_map = {
            "journal_articles": ["Journal Articles"],
            "books": ["Books / Monographs"],
            "websites": ["Websites"],
            "online_videos": ["Online Videos"],
            "news_sources": ["News Sources"],
            "social_media": ["Social Media"],
            "theses": ["Theses / Dissertations"],
        }
        target_names = topic_map.get(subtype, [])
        filtered = [t for t in all_topics if t.get("topic") in target_names]

        return {
            "category": category_data.get("category", "Secondary Sources"),
            "topics": filtered,
        }

    direct_map = {
        "jurisprudence": "jurisprudence",
        "legislation": "legislation",
        "government_docs": "government_docs",
        "general_rules": "general_rules",
    }

    key = direct_map.get(detected_type)
    if key and key in rules_db:
        return {
            "category": rules_db[key].get("category", detected_type),
            "topics": rules_db[key].get("topics", []),
        }

    return {"category": "General Rules", "topics": []}


def build_prompt(extracted_fields: dict, detected_type: str, relevant_rules: dict) -> str:
    """将字段与规则拼成传给 DeepSeek 的 prompt。"""
    rules_text = json.dumps(relevant_rules, ensure_ascii=False, indent=2)
    rules_text = rules_text.replace(" | ", " ").replace("|", "")
    fields_text = json.dumps(extracted_fields, ensure_ascii=False, indent=2)

    # 按来源类型添加斜体规则（mcgill_rules.json 要求）
    italic_rules = ""
    if detected_type == "jurisprudence":
        italic_rules = (
            "- YOU MUST italicize the case name using Markdown *asterisks*.\n"
            "  Example: *R v Sharma*, 2022 SCC 39, [2022] 3 SCR 147.\n"
        )
    elif detected_type == "legislation":
        italic_rules = (
            "- YOU MUST italicize the statute title using Markdown *asterisks*,\n"
            "  followed by a non-italicized comma.\n"
            "  Example: *Criminal Code*, RSC 1985, c C-46.\n"
        )
    elif detected_type == "secondary_sources.journal_articles":
        italic_rules = (
            "- YOU MUST italicize journal names using Markdown *asterisks*.\n"
            "  Article titles go in quotation marks, not italics.\n"
            "  Example: Jane Smith, \"Article Title\" (2020) 45 *McGill LJ* 123.\n"
        )
    elif detected_type == "secondary_sources.books":
        italic_rules = (
            "- YOU MUST italicize book titles using Markdown *asterisks*.\n"
            "  Example: Jane Smith, *Book Title*, 2nd ed (Publisher, 2020).\n"
        )
    elif detected_type == "government_docs":
        italic_rules = (
            "- Do NOT italicize Indigenous constitutional documents.\n"
        )

    return f"""You are a McGill legal citation formatter.
Format the following information into a proper McGill citation.

Source type: {detected_type}

McGill Rules for this source type:
{rules_text}

Information to format:
{fields_text}

STRICT OUTPUT RULES:
- Output ONLY the McGill citation, nothing else
- No sentences like "can be found online at" or "is available at"
- No numbers or bullets at the start
- No explanation, no commentary
- Follow EXACTLY the template structure shown in the McGill Rules above
- Replace placeholder words like Author, Title, Date with the actual values from the information provided
- If a field is null or missing, omit it entirely
- For websites: Author (if any), "Title", (Date), online: Site Name <URL>.
- Output must end with a period
{italic_rules}"""


def format_citation(extracted_fields: dict, doc_type: str | None = None) -> str:
    """对外主入口：自动判断类型 → 取规则 → 拼 prompt → 调 DeepSeek → 返回引用。

    Args:
        extracted_fields: 从文件/URL 提取的结构化字段。
        doc_type: 可选。LLM 分类的文档类型（如 "case"、"journal_article"），
                  不为 None 时覆盖 detect_type() 的结果。
    """
    if doc_type is not None:
        type_map = {
            "case":                "jurisprudence",
            "legislation":         "legislation",
            "government_document": "government_docs",
            "journal_article":     "secondary_sources.journal_articles",
            "book":                "secondary_sources.books",
            "book_chapter":        "secondary_sources.books",
            "thesis":              "secondary_sources.theses",
            "newspaper":           "secondary_sources.news_sources",
            "website":             "secondary_sources.websites",
            "report":              "government_docs",
            "other":               "general_rules",
        }
        detected_type = type_map.get(doc_type, "general_rules")
    else:
        detected_type = detect_type(extracted_fields)
    relevant_rules = get_rules(detected_type)
    prompt = build_prompt(extracted_fields, detected_type, relevant_rules)
    t0 = time.time()
    result = ask_deepseek(prompt)
    if timing.ENABLE_TIMING:
        timing.report().add_llm("format_citation", time.time() - t0)
    global _last_prompt, _last_raw_response
    _last_prompt = prompt
    _last_raw_response = result
    return result
