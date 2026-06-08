import json

from llm_api.local_ollama import ask_ollama, chat_ollama
from llm_api.kimi_api import extract_from_url
from local_tools.a2aj_api import fetch_by_citation
from local_tools.file_extractor import extract_from_file
from local_tools.citation_tracker import CitationTracker
from core.mcgill_engine import format_citation


def route(input_type: str, content: str, tracker: CitationTracker = None) -> str:

    # 本地：A2AJ citation 查询
    if input_type == "a2aj":
        extracted_fields = fetch_by_citation(content)
        result = format_citation(extracted_fields)
        CitationTracker().auto_add(result)
        return result

    # 本地：文件提取
    elif input_type == "file":
        extracted_fields = extract_from_file(content)
        # 文件内容可能较复杂，先用 Ollama 提取结构化字段
        raw_text = extracted_fields.pop("raw_text", "")
        if raw_text and not extracted_fields.get("author"):
            prompt = f"""Extract citation fields from the following text and return JSON only.
Possible fields: author, title, journal, volume, issue, year, publisher,
place_of_publication, url, newspaper, degree, institution, neutral_citation,
style_of_cause, reporter, jurisdiction, chapter, issuing_body.

Text: {raw_text}

Return JSON only, no explanation."""
            try:
                response = ask_ollama(prompt)
                extracted_fields.update(json.loads(response))
            except Exception:
                pass
        result = format_citation(extracted_fields)
        CitationTracker().auto_add(result)
        return result

    # 本地：ibid/supra 管理
    elif input_type == "ibid":
        if tracker is None:
            return "[错误:ibid/supra 需要传入 CitationTracker 实例]"
        params = json.loads(content)
        return tracker.get_reference(
            params["footnote_num"],
            params["target"],
            params.get("pinpoint", "")
        )

    # 本地：Ollama 自由咨询（不走RAG，直接返回）
    elif input_type == "chat":
        return chat_ollama([{"role": "user", "content": content}])

    # 联网：Kimi 提取 → RAG 格式化
    elif input_type == "llm":
        extracted_fields = extract_from_url(content)
        result = format_citation(extracted_fields)
        CitationTracker().auto_add(result)
        return result

    return "[错误：未知的 input_type]"