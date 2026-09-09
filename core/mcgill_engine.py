import json
import re
import time
import logging

from llm_api.deepseek_api import ask_deepseek

logger = logging.getLogger(__name__)
from local_tools import timing_util as timing
from local_tools.crossref_api import extract_doi, fetch_crossref, build_journal_citation
from local_tools.openlibrary_api import extract_isbn, validate_isbn, fetch_openlibrary, build_book_citation
from profiling import timing as prof

import os
RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcgill_rules.json")

# 调试缓存：最近一次 format_citation 的 prompt、原始返回、数据源
_last_prompt = None
_last_raw_response = None
_last_source = None
_last_asterisk_guard = None


def get_last_debug() -> dict:
    """返回最近一次 format_citation 的调试信息。"""
    return {
        "prompt": _last_prompt,
        "raw_response": _last_raw_response,
        "source": _last_source,
        "asterisk_guard": _last_asterisk_guard,
    }


# 宪法性文件标题封闭集合（不依赖 A2AJ 验证）
CONSTITUTIONAL_TITLES = {
    "canadian charter of rights and freedoms",
    "constitution act, 1867",
    "constitution act, 1982",
    "canada act 1982",
}

# 编号型法规前缀（用于 leg.regulation_numbered 判别）
_NUMBERED_REG_PATTERN = re.compile(
    r'^(SOR|SI|CRC|O\s*Reg|Alta\s*reg|Man\s*Reg|NS\s*Reg|NB\s*Reg|'
    r'Nfld\s*Reg|NWT\s*Reg|Nu\s*Reg|PEI\s*Reg|Sask\s*Reg|'
    r'Yukon\s*Reg|BC\s*Reg|Ont\s*Reg|Que\s*Reg)[/\s,]',
    re.IGNORECASE
)


def _is_numbered_regulation(title: str) -> bool:
    """Check if a statute_title is a numbered-only regulation (no descriptive text)."""
    return bool(_NUMBERED_REG_PATTERN.match(title.strip()))


# ═══════════════════════════════════════════════════════════════════
#  SUBPATTERN_TEMPLATES — 子模式对应的 template + 1~2 条 examples
#  TODO(clean-room): 回收进 mcgill_rules.json 后删此常量，改为从 JSON 加载
# ═══════════════════════════════════════════════════════════════════

SUBPATTERN_TEMPLATES: dict[str, dict] = {
    "juris.neutral": {
        "category": "Jurisprudence — Neutral Citation Only",
        "topics": [
            {
                "topic": "Neutral Citation (no parallel)",
                "template": "",
                # Source: mcgill_rules.json > jurisprudence > Neutral Citation ex[0]
                "examples": [
                    "R v King, 2002 SCC 10."
                ],
            }
        ],
    },
    "juris.neutral_parallel": {
        "category": "Jurisprudence — Neutral + Parallel",
        "topics": [
            {
                "topic": "Neutral Citation with Parallel Reporter",
                "template": "",
                # Source: mcgill_rules.json > jurisprudence > Neutral Citation ex[1]
                "examples": [
                    "R v King, 2002 SCC 10, [2002] 1 SCR 227."
                ],
            }
        ],
    },
    # [REVIEW: Shixian] 待 McGill 10th 确认权威。
    # 此格式 (StyleOfCause, Reporter) 在 mcgill_rules.json jurisprudence topics 中无归类依据，
    # 但 "R v Gladue, [1999] 1 SCR 688." (见于 Ibid for Cases topic) 形态一致，暂保留。
    # 例字符串不会注入 prompt；prompt 接收时以"{category}"说明替代。
    "juris.reported_only": {
        "category": "Jurisprudence — Reported Only (no neutral)",
        "topics": [
            {
                "topic": "Reporter Only",
                "template": "",
                "examples": [
                    "R v Oakes, [1986] 1 SCR 103."
                ],
            }
        ],
    },
    "leg.statute": {
        "category": "Legislation — Statutes (descriptive title)",
        "topics": [
            {
                "topic": "Statutes – General Form",
                "template": "Title, | statute volume | jurisdiction | year, | chapter, | other indexing elements, | (session or supplement), | pinpoint",
                # Source: mcgill_rules.json > legislation > Statutes – General Form ex[2]
                "examples": [
                    "Criminal Code, RSC 1985, c C-46, s 718.2(e)."
                ],
            }
        ],
    },
    "leg.constitutional": {
        "category": "Legislation — Constitutional Statutes",
        "topics": [
            {
                "topic": "Constitutional Statutes",
                "template": "*Title*, | constitutional_reference | jurisdiction | year, | chapter, | other_info | pinpoint",
                # Source: mcgill_rules.json > legislation > Constitutional Statutes ex[0]
                "examples": [
                    "*Constitution Act, 1982*, s 35, being Schedule B to the *Canada Act 1982* (UK), 1982, c 11."
                ],
            }
        ],
    },
    "gov.parliamentary_documents": {
        "category": "Government Documents — Parliamentary Documents (Hansard)",
        "topics": [
            {
                "topic": "Parliamentary Documents",
                "template": "Jurisdiction, | legislature, | title, | legislative session, | number | (date) | pinpoint | (speaker) | online: | <URL> | [archived URL].",
                "rules": [
                    "Federal House of Commons Debates use compact NN-N session form and omit 'Canada,' prefix.",
                    "Provincial/territorial legislatures include jurisdiction prefix (e.g. 'Quebec, National Assembly').",
                    "Use 'No' for issue number, NOT 'vol'.",
                    "Provide the date of the debate in parentheses.",
                    "Indicate the speaker and pinpoint reference where applicable."
                ],
                "examples": [
                    "House of Commons Debates, 37-1, No 64 (17 May 2001) at 7175 (Hon Elinor Caplan).",
                    "House of Commons Debates, 42-1, No 372 (28 January 2019) at 24850 (Hon Geoff Regan) online: <ourcommons.ca> [perma.cc/33G-DPUN].",
                ],
            }
        ],
    },
    "gov.committee_reports": {
        "category": "Government Documents — Committee Reports",
        "topics": [
            {
                "topic": "Committee Reports",
                "template": "Jurisdiction, | legislative body, | committee name, | *title*, | legislative session, | number | (date) | pinpoint | (speaker), | online: | <URL>.",
                "rules": [
                    "Include the jurisdiction and legislative body.",
                    "Provide the full committee name.",
                    "Italicize the report title using Markdown asterisks.",
                    "Use compact NN-N session form for federal committee documents.",
                    "Include the date and optional pinpoint/speaker."
                ],
                "examples": [
                    "House of Commons, Standing Committee on Justice and Human Rights, *Evidence*, 39-2, No 12 (7 February 2008) at 15:30 (Tony Cannavino).",
                ],
            }
        ],
    },
    "gov.inquiry_reports": {
        "category": "Government Documents — Reports on Inquiries and Commissions",
        "topics": [
            {
                "topic": "Reports on Inquiries and Commissions",
                "template": "Jurisdiction, | issuing body, | title, | volume | (publication information) | (Chair) | pinpoint.",
                "rules": [
                    "Include the jurisdiction unless it is mentioned in another element of the citation.",
                    "Include the issuing body unless it is mentioned in the title of the report.",
                    "To distinguish between volumes, indicate vol or any other appellation used in the report.",
                ],
                "examples": [
                    "Commission of Inquiry on the Blood System in Canada: Final Report, vol 1 (Ottawa: Public Works and Governmental Services Canada, 1997) at 100.",
                ],
            }
        ],
    },
    "general_rules": {
        "category": "General Source — Citation",
        "topics": [
            {
                "topic": "Statute/Regulation (identified by statute_title)",
                "template": "Title, jurisdiction year, chapter, pinpoint.",
                "rules": [
                    "If statute_title is present, it is the Title.",
                    "If jurisdiction and citation are present, include them after the Title.",
                    "If chapter is present, include it.",
                    "If ONLY statute_title is present with no other data, output just the Title and end with a period.",
                    "If NO field is useful, output just the Title or leave a minimal placeholder.",
                ],
                "examples": [
                    "Criminal Code, RSC 1985, c C-46.",
                    "Employment Standards Act.",
                ],
            },
            {
                "topic": "Document/Report (identified by title, author, date)",
                "template": "Author, Title (Date).",
                "rules": [
                    "If author is present: Author, *Title* (Date).",
                    "If no author: *Title* (Date).",
                    "Italicize document/report titles using Markdown *asterisks*.",
                    "If date is missing, omit the parentheses.",
                    "If publisher is available, include as (Place: Publisher, Year).",
                ],
                "examples": [
                    "python-docx, *Test Document Title* (2013).",
                ],
            },
        ],
    },
}


def _normalize_title(title: str) -> str:
    """Normalize title for CONSTITUTIONAL_TITLES matching: lowercase, strip, remove commas, collapse whitespace."""
    import re as _re
    return _re.sub(r'\s+', ' ', title.lower().strip().replace(',', ''))


# Pre-normalized CONSTITUTIONAL_TITLES for matching (same normalization applied at definition time)
_NORMALIZED_CONSTITUTIONAL_TITLES = {_normalize_title(t) for t in CONSTITUTIONAL_TITLES}


def select_subpattern(detected_type: str, fields: dict) -> str | None:
    """Deterministically select a subpattern based on detected_type and available fields.

    Pure function — no LLM, no side effects, no I/O.
    Returns a subpattern key (e.g. 'juris.neutral') or None to fall back to full-topic behavior.

    Jurisprudence priority:
        neutral + reporter       → juris.neutral_parallel
        neutral, no reporter     → juris.neutral
        no neutral, has reporter → juris.reported_only
        otherwise                → None
        (juris.unreported: intentionally None — no authoritative example in mcgill_rules.json)

    Legislation priority:
        title matches CONSTITUTIONAL_TITLES  → leg.constitutional
        has descriptive title                → leg.statute
        otherwise                            → None
        (leg.regulation_numbered: intentionally None — no authoritative example in mcgill_rules.json)

    Other detected_type → always returns None.
    """
    if detected_type == "jurisprudence":
        has_neutral = bool(fields.get("neutral_citation"))
        has_reporter = bool(fields.get("reporter"))

        if has_neutral and has_reporter:
            return "juris.neutral_parallel"
        if has_neutral and not has_reporter:
            return "juris.neutral"
        if not has_neutral and has_reporter:
            return "juris.reported_only"
        # juris.unreported: not enough authoritative examples in mcgill_rules.json.
        # Falls back to full-topic LLM behavior (return None).
        return None

    if detected_type == "legislation":
        statute_title = (fields.get("statute_title") or fields.get("title") or "").strip()
        if not statute_title:
            return None

        # Constitutional — check normalized title prefix
        title_normalized = _normalize_title(statute_title)
        if any(title_normalized.startswith(t) for t in _NORMALIZED_CONSTITUTIONAL_TITLES):
            return "leg.constitutional"

        # Numbered-only regulations (SOR/xxxx, O Reg xxx/xx, etc.):
        # No authoritative examples in mcgill_rules.json → return None to fall back to full-topic LLM behavior.
        if _is_numbered_regulation(statute_title):
            return None

        # Has descriptive title → statute
        if statute_title:
            return "leg.statute"

        return None

    if detected_type == "government_docs":
        raw_text = fields.get("raw_text", "") or ""
        from local_tools.file_extractor import classify_gov_doc_subtype
        subtype = classify_gov_doc_subtype(raw_text)
        if subtype == "parliamentary_documents":
            return "gov.parliamentary_documents"
        if subtype == "committee_reports":
            return "gov.committee_reports"
        if subtype == "inquiry_reports":
            return "gov.inquiry_reports"
        return None

    if detected_type == "general_rules":
        return "general_rules"

    return None


def detect_type(extracted_fields: dict) -> str:
    """根据提取字段自动判断 McGill 引用类型。"""
    # Presence = non-blank value.  Blank-string markers (e.g. website="") must
    # not count as fields — same absence semantics as the prompt filter.
    keys = {
        k.lower().replace(" ", "_")
        for k, v in extracted_fields.items()
        if v is not None and str(v).strip()
    }

    def has(*candidates):
        return any(c in keys for c in candidates)

    # 0. Role-trust early return: concept-route legislation items carry explicit
    #    "role": "legislation" set by verify_one / _verify_legislation.
    #    The any()-based jurisprudence check below would match on neutral_citation
    #    alone and misclassify them.  This contract must be preserved if detect_type
    #    or a unified composer is refactored — removing it regresses concept-route
    #    legislation classification (past Charter-class bug SCC 2020-001).
    if extracted_fields.get("role") == "legislation":
        return "legislation"

    # 1. Jurisprudence
    if has("style_of_cause", "neutral_citation", "reporter"):
        return "jurisprudence"

    # 1.5 Constitutional statutes（按标题前缀匹配，不依赖 A2AJ 字段）
    title_val = (extracted_fields.get("statute_title") or "").strip().lower()
    title_val_norm = _normalize_title(title_val)
    if any(title_val_norm.startswith(t) for t in _NORMALIZED_CONSTITUTIONAL_TITLES):
        return "constitutional_statutes"

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

    return "general_rules"


_RULES_DB_CACHE: dict | None = None


def _load_rules_db() -> dict:
    """Load mcgill_rules.json once per process.

    Rules change only on deploy, so a process-lifetime cache is safe; this
    used to re-read and re-parse the ~50 KB file on every full-topic
    format_citation call.
    """
    global _RULES_DB_CACHE
    if _RULES_DB_CACHE is None:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            _RULES_DB_CACHE = json.load(f)
    return _RULES_DB_CACHE


def get_rules(detected_type: str, subpattern: str | None = None) -> dict:
    """从 mcgill_rules.json 读取并返回与类型相关的规则片段。

    Args:
        detected_type: detect_type() 或 doc_type 映射的结果。
        subpattern: 可选。当不为 None 时，只返回该子模式对应的 template + 1~2 条 examples，
                    不返回整个 type 的所有 topics。
    """
    # ── Subpattern mode: return narrow slice ──
    if subpattern and subpattern in SUBPATTERN_TEMPLATES:
        return dict(SUBPATTERN_TEMPLATES[subpattern])

    # ── Full-topic mode (original behavior, subpattern is None) ──
    rules_db = _load_rules_db()

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

    if detected_type == "constitutional_statutes":
        cat_data = rules_db.get("legislation", {})
        all_t = cat_data.get("topics", [])
        filtered = [t for t in all_t if t.get("topic") == "Constitutional Statutes"]
        return {
            "category": "Legislation",
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


# ═════════════════════════════════════════════════════════════════════════════
#  A0 — Type-name mapping table
#  Three namespaces refer to the same citation types:
#    (a) keys/paths in mcgill_rules.json
#    (b) type strings passed to _build_italic_rules()
#    (c) deterministic builder functions in local_tools/
#
#  canonical    | rules-JSON key/path              | _build_italic_rules type    | builder function
#  -------------|----------------------------------|-----------------------------|--------------------
#  jurisprudence | jurisprudence (top-level)        | jurisprudence                | — (LLM only)
#  legislation   | legislation (top-level)          | legislation                  | — (LLM only)
#    statute     | legislation > Statutes           | (handled by subpattern)      | — (LLM only)
#    constitutional | legislation > Constitutional  | constitutional_statutes      | — (LLM only)
#    bill        | legislation > Bills              | — (gap: italic via builder)  | build_bill_citation
#    regulation  | legislation > (by-law, securities)|— (gap: no dedicated rule)  | — (LLM only)
#  government    | government_docs (top-level)      | government_docs              | — (LLM only)
#  journal       | secondary_sources > Journal      | secondary_sources.journal_articles | build_journal_citation
#  book          | secondary_sources > Books        | secondary_sources.books      | build_book_citation
#  by_law        | by_law (top-level scaffold key)  | — (gap)                     | — (scaffold only)
#  treaty        | treaty (top-level scaffold key)  | — (gap)                     | — (scaffold only)
#  foreign       | foreign (top-level scaffold key) | — (gap)                     | — (scaffold only)
#  news_online   | news_online (top-level scaffold) | — (gap)                     | — (scaffold only)
#  website       | website (top-level scaffold key) | secondary_sources.websites   | — (LLM only)
#  general       | general_rules (top-level)        | general_rules (fallback)     | — (LLM only)
#
#  Key: "gap" = the type appears in one namespace but has no equivalent in
#  another — e.g. bill has a builder but no _build_italic_rules entry (italic
#  is applied directly in the builder).  Do NOT treat "key not found" as
#  "no italic rule" — it may simply be handled elsewhere.
# ═════════════════════════════════════════════════════════════════════════════

def _build_italic_rules(detected_type: str, subpattern: str | None = None) -> str:
    """按 detected_type / subpattern 生成斜体规则文本。

    subpattern 不为 None 时优先按子模式选规则，不再让 LLM 自己判断。
    subpattern 为 None 时维持原有的 detected_type 行为。
    """
    # ── Subpattern-specific italic rules ──
    if subpattern:
        if subpattern.startswith("juris."):
            # All jurisprudence subpatterns italicize the case name
            return (
                "- YOU MUST italicize the case name using Markdown *asterisks*.\n"
                "  Example: *R v Sharma*, 2022 SCC 39, [2022] 3 SCR 147.\n"
            )

        if subpattern == "leg.statute":
            return (
                "- YOU MUST italicize the title using Markdown *asterisks*, followed by a non-italicized comma.\n"
                "  Example: *Criminal Code*, RSC 1985, c C-46.\n"
            )
        if subpattern == "leg.constitutional":
            return (
                "- YOU MUST italicize the title of the constitutional statute using Markdown *asterisks*.\n"
                "  Example: *Constitution Act, 1982*, s 35, being Schedule B to the *Canada Act 1982* (UK), 1982, c 11.\n"
                "  Example: *Canadian Charter of Rights and Freedoms*, s 7, Part I of the *Constitution Act, 1982*, being Schedule B to the *Canada Act 1982* (UK), 1982, c 11.\n"
            )

        if subpattern == "gov.committee_reports":
            return (
                "- YOU MUST italicize the report title using Markdown *asterisks*.\n"
                "  Example: Standing Committee on Access to Information, Privacy and Ethics, *Report on the Protection of Personal Information in the Digital Age*, 4th Report, 44th Parl, 1st Sess (December 2025).\n"
            )

        if subpattern == "gov.inquiry_reports":
            return (
                "- Do NOT italicize Indigenous constitutional documents.\n"
                "- Follow the template: Jurisdiction, issuing body, title, volume (publication information) (Chair) pinpoint.\n"
            )

        if subpattern == "gov.parliamentary_documents":
            return (
                "- Do NOT italicize the legislative body or session name.\n"
                "- Follow the template: Jurisdiction, legislative body, session, volume (date) pinpoint (speaker), online: <URL>.\n"
            )

        if subpattern == "general_rules":
            return (
                "- If the source has a statute_title, do NOT italicize it (it is a statute/regulation title in Roman).\n"
                "- If the source has a document/report title, italicize it using Markdown *asterisks*.\n"
                "  Example: python-docx, *Test Document Title* (2013).\n"
            )

        # Fallback for unknown subpattern: no italic instruction
        return ""

    # ── Legacy detected_type-based italic rules (original behavior) ──
    italic_rules = ""
    if detected_type == "jurisprudence":
        italic_rules = (
            "- YOU MUST italicize the case name using Markdown *asterisks*.\n"
            "  Example: *R v Sharma*, 2022 SCC 39, [2022] 3 SCR 147.\n"
        )
    elif detected_type == "legislation":
        italic_rules = (
            "- CASE A: Statutes and regulations WITH a descriptive title —\n"
            "  italicize the title using Markdown *asterisks*, followed by a non-italicized comma.\n"
            "  Example: *Criminal Code*, RSC 1985, c C-46.\n"
            "  Example: *Migratory Birds Regulations*, CRC, c 1035, s 4.\n"
            "- CASE B: Regulations WITHOUT a descriptive title\n"
            "  (identified only by a number like SOR/2000-111 or O Reg 426/00) —\n"
            "  Do NOT italicize anything. Output the entire citation in Roman as-is.\n"
        )
    elif detected_type == "secondary_sources.journal_articles":
        italic_rules = (
            "- YOU MUST italicize the journal name using Markdown *asterisks*.\n"
            "  The article title goes in quotation marks, NOT italics.\n"
            "  Author list: 1 author \"A\"; 2 authors \"A & B\"; 3 authors \"A, B & C\"; 4+ \"A et al\".\n"
            "  Example: David M Tanovich, \"E-Racing Racial Profiling\" (2004) 41 *Alta L Rev* 905.\n"
        )
    elif detected_type == "secondary_sources.books":
        italic_rules = (
            "- YOU MUST italicize book titles using Markdown *asterisks*.\n"
            "  Example: Jane Smith, *Book Title*, 2nd ed (Toronto: Carswell, 2020).\n"
        )
    elif detected_type == "constitutional_statutes":
        italic_rules = (
            "- YOU MUST italicize the title of the constitutional statute using Markdown *asterisks*.\n"
            "  Example: *Constitution Act, 1982*, s 35, being Schedule B to the *Canada Act 1982* (UK), 1982, c 11.\n"
            "  Example: *Canadian Charter of Rights and Freedoms*, s 7, Part I of the *Constitution Act, 1982*, being Schedule B to the *Canada Act 1982* (UK), 1982, c 11.\n"
        )
    elif detected_type == "government_docs":
        italic_rules = (
            "- Do NOT italicize Indigenous constitutional documents.\n"
        )

    # ── EXTENSION (not implemented yet): ──
    # 1) Short titles [Bell] — italicized for cases and statutes (McGill 10th p5).
    # 2) ibid / supra — italicized (McGill 10th p6).
    # These will be added as italic_rules and/or a post-processing step in the Extension phase.
    # ── End of Extension notes ──

    return italic_rules


def build_prompt(extracted_fields: dict, detected_type: str, relevant_rules: dict, subpattern: str | None = None) -> str:
    """将字段与规则拼成传给 DeepSeek 的 prompt。

    当 subpattern 不为 None 时，斜体规则按子模式选取，不再让 LLM 做 CASE A/B 判断。
    字段过滤只发生在序列化成 prompt 字符串的这一刻——extracted_fields 字典本体
    绝不修改（raw_text 在本函数之前有三个真实消费者：DOI 路径、ISBN 路径、
    select_subpattern 的 government_docs 子类型判别）。
    """
    rules_text = json.dumps(relevant_rules, ensure_ascii=False, indent=2)
    rules_text = rules_text.replace(" | ", " ").replace("|", "")
    fields_text = json.dumps(_prompt_fields(extracted_fields), ensure_ascii=False, indent=2)

    italic_rules = _build_italic_rules(detected_type, subpattern)

    strict_rules = [
        "- Output ONLY the McGill citation, nothing else",
        '- No sentences like "can be found online at" or "is available at"',
        "- No numbers or bullets at the start",
        "- No explanation, no commentary",
        "- Follow EXACTLY the template structure shown in the McGill Rules above",
        "- Replace placeholder words like Author, Title, Date with the actual values from the information provided",
        "- If a field is null or missing, omit it entirely",
        "- Output must end with a period",
        '- NEVER add a pinpoint (e.g. "at para 42", "s 7(2)", "at 100") that is not explicitly present in the input fields above. Only include a pinpoint if the input fields contain a non-null value for it.',
    ]
    if detected_type in ("secondary_sources.websites", "secondary_sources.news_sources"):
        strict_rules.append(
            '- For web sources: Author (if any), "Title", (Date), online: <site domain> [archived URL].'
        )
    strict_text = "\n".join(strict_rules)

    return f"""You are a McGill legal citation formatter.
Format the following information into a proper McGill citation.

Source type: {detected_type}

McGill Rules for this source type:
{rules_text}

Information to format:
{fields_text}

STRICT OUTPUT RULES:
{strict_text}
{italic_rules}"""


# ── Prompt hygiene: serialization-time field filtering ──────────────────────
# 批量正文与管线内部字段绝不进 prompt：raw_text 是注入面也是 token 大头；
# hostname/verified/warning/display/role 是路由或前端元数据，对排版毫无意义。
_PROMPT_EXCLUDED_FIELDS = frozenset({
    "raw_text",     # 全文正文（URL 路径不截断）——注入面 + token 大头
    "hostname",     # 路由元数据
    "verified",     # 管线标志位
    "warning",      # search_citation 的中文复核提示
    "display",      # /citation/select 回传的前端标签
    "role",         # concept 路由元数据（detect_type 已消费）
})

_MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")


def _humanize_date(value):
    """ISO 日期 → McGill 散文日期（'2017-04-25' → '25 April 2017'）。

    仅对 ISO 形状生效；已经是散文日期、部分垃圾、非字符串一律原样返回。
    纯函数——结果只用于 prompt 序列化，禁止写回 fields 字典。
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{d} {_MONTHS_EN[mo - 1]} {y}"
        return s
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return f"{_MONTHS_EN[mo - 1]} {m.group(1)}"
    return s


def _prompt_fields(extracted_fields: dict) -> dict:
    """Serialization-time field filter — returns a NEW dict.

    The input dict is never modified: raw_text/None filtering here must not
    leak into the DOI/ISBN deterministic paths or select_subpattern, all of
    which read the ORIGINAL fields before build_prompt is reached.  Blank
    strings count as absence (same semantics detect_type applies).
    """
    out = {}
    for k, v in extracted_fields.items():
        if k in _PROMPT_EXCLUDED_FIELDS or k.startswith("_"):
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if k == "date":
            v = _humanize_date(v)
        out[k] = v
    return out


# ── Per-process first-call tracking for format_citation ──
_first_format = True


def _mark_first_format() -> bool:
    global _first_format
    if _first_format:
        _first_format = False
        return True
    return False


import time as _fmt_time


# ── Deterministic-lookup failure signals ────────────────────────────────────
# ValueError subclasses so existing ``except ValueError`` handlers keep
# working; typed so callers branch on the failure KIND instead of matching
# English message substrings (which silently breaks when wording changes).
class NotADoiError(ValueError):
    """No DOI could be extracted from the input."""


class DoiNotFoundError(ValueError):
    """DOI extracted but the provider has no record / was unreachable."""


class InvalidIsbnError(ValueError):
    """ISBN extracted but its checksum fails."""


class IsbnNotFoundError(ValueError):
    """ISBN valid but the provider has no record / was unreachable."""


def _ensure_balanced_asterisks(citation: str) -> str:
    """Guarantee renderable italics: the number of '*' must be even.

    奇数个星号时先尝试修复——在首星号之后的第一个 ', ' 边界补右星号
    （最常见的失败形态是案名的闭合星号在逗号前被丢掉）。无法定位边界
    时剥掉全部星号：渲染成纯文本是看得见的 McGill 降级，好过渲染坏掉。
    两种处置都记日志并通过 get_last_debug() 暴露，绝不静默。
    """
    global _last_asterisk_guard
    if citation.count("*") % 2 == 0:
        return citation
    first = citation.find("*")
    boundary = citation.find(", ", first + 1) if first != -1 else -1
    if boundary != -1:
        repaired = citation[:boundary] + "*" + citation[boundary:]
        _last_asterisk_guard = (
            f"repaired: closed italic run before first ', ' (offset {boundary})"
        )
        logger.warning("[asterisk-guard] %s | %r", _last_asterisk_guard, repaired[:80])
        return repaired
    stripped = citation.replace("*", "")
    _last_asterisk_guard = "stripped: no ', ' boundary after opening '*'"
    logger.warning("[asterisk-guard] %s | %r", _last_asterisk_guard, stripped[:80])
    return stripped


def format_citation(extracted_fields: dict, doc_type: str | None = None) -> str:
    """对外主入口：自动判断类型 → 取规则 → 拼 prompt → 调 DeepSeek → 返回引用。

    Args:
        extracted_fields: 从文件/URL 提取的结构化字段。
        doc_type: 可选。LLM 分类的文档类型（如 "case"、"journal_article"），
                  不为 None 时覆盖 detect_type() 的结果。
    """
    _f_t0 = _fmt_time.perf_counter()
    _is_first_fmt = _mark_first_format()
    if _is_first_fmt:
        logger.debug("[DUR] format_citation — FIRST call")
    global _last_prompt, _last_raw_response, _last_source, _last_asterisk_guard
    _last_source = None
    _last_asterisk_guard = None

    # ── Bill 确定性路径（LEGISinfo，不过 LLM） ──
    bill_cit = extracted_fields.get("_bill_citation")
    if bill_cit:
        _last_prompt = "[LEGISinfo] " + (extracted_fields.get("style_of_cause", ""))
        _last_raw_response = bill_cit
        _last_source = "legisinfo"
        _f_elapsed = _fmt_time.perf_counter() - _f_t0
        logger.debug("[DUR] format_citation END (bill_path) — %.1fms  first=%s", _f_elapsed * 1000, _is_first_fmt)
        return bill_cit

    # ── CrossRef 优先路径（仅 journal_article） ──
    # On failure signals via ValueError so callers never emit raw DOI as a citation.
    if doc_type == "journal_article":
        raw_text = extracted_fields.get("raw_text", "") or ""
        url = extracted_fields.get("url", "") or ""
        doi = extract_doi(raw_text) or extract_doi(url)
        if doi:
            cr_data = fetch_crossref(doi)
            if cr_data:
                result = build_journal_citation(cr_data)
                _last_prompt = f"[CrossRef] DOI: {doi}"
                _last_raw_response = result
                _last_source = "crossref"
                return result
            # DOI valid but CrossRef has no data / unreachable
            raise DoiNotFoundError(
                "Couldn't find this publication in our databases."
            )
        # No DOI extracted from input — signal failure instead of falling through to LLM
        raise NotADoiError(
            "This doesn't look like a valid DOI — please check the identifier."
        )

    # ── Open Library 优先路径（仅 book，镜像 CrossRef 写法） ──
    # On failure signals via ValueError so callers never emit the raw ISBN as a citation.
    if doc_type == "book":
        raw_text = extracted_fields.get("raw_text", "") or ""
        isbn = extract_isbn(raw_text)
        if isbn:
            if not validate_isbn(isbn):
                raise InvalidIsbnError(
                    "This ISBN appears invalid — please check the digits."
                )
            ol_data = fetch_openlibrary(isbn)
            if ol_data:
                result = build_book_citation(ol_data)
                if result:
                    _last_prompt = f"[OpenLibrary] ISBN: {isbn}"
                    _last_raw_response = result
                    _last_source = "openlibrary"
                    return result
            # Valid ISBN but Open Library has no data / unreachable
            raise IsbnNotFoundError(
                "Couldn't find this book in our databases."
            )
        # No valid ISBN extracted from raw_text — fall through to generic formatting

    # ── 常规 type → detect_type 映射 ──
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

    # ── 第二层：确定性子模式路由 ──
    subpattern = select_subpattern(detected_type, extracted_fields)

    # ── 按子模式（或全 topics）取规则 ──
    relevant_rules = get_rules(detected_type, subpattern=subpattern)
    prompt = build_prompt(extracted_fields, detected_type, relevant_rules, subpattern=subpattern)

    t0 = time.time()
    with prof.measure("llm.format", model=os.getenv("LLM_DEFAULT_MODEL", "deepseek-v4-flash")):
        result = ask_deepseek(prompt, disable_thinking=True)
    if timing.ENABLE_TIMING:
        timing.report().add_llm("format_citation", time.time() - t0)
    _last_prompt = prompt
    _last_raw_response = result
    result = _ensure_balanced_asterisks(result)
    if _last_source is None:
        _last_source = "deepseek"
    _f_elapsed = _fmt_time.perf_counter() - _f_t0
    logger.debug("[DUR] format_citation END (llm_path) — %.1fms  first=%s", _f_elapsed * 1000, _is_first_fmt)
    return result
