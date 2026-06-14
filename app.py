"""McGill Citation Tool — Gradio 前端（四Tab版）"""

import time
import gradio as gr
from local_tools.citation_search import search_citation, classify_and_normalize
from local_tools.file_extractor import extract_from_file, classify_document_type
from local_tools import timing_util as timing
from llm_api.deepseek_api import extract_from_url, chat_deepseek
from core.mcgill_engine import format_citation, get_last_debug, detect_type, get_rules


# ═══════════════════════════════════════════════
# Tab 1 — Citation 查询
# ═══════════════════════════════════════════════

def _plain_text(md_text: str) -> str:
    """去除 Markdown 标记（*），供纯文本复制用。"""
    if not md_text:
        return ""
    return md_text.replace("*", "")


def _a2aj_summary(item: dict) -> str:
    """Format a single A2AJ result item as a debug string."""
    if not item:
        return "未命中"
    if item.get("style_of_cause"):
        return f"案件: {item['style_of_cause']}  —  {item.get('neutral_citation','')}"
    if item.get("statute_title"):
        cit = item.get('neutral_citation') or ''
        jur = item.get('jurisdiction') or ''
        ch = item.get('chapter') or ''
        return f"法规: {item['statute_title']}  —  {cit}  juris={jur}  ch={ch}"
    if item.get("name"):
        return f"名称: {item['name']}  —  {item.get('neutral_citation','')}"
    if item.get("warning"):
        return f"⚠️ {item['warning'][:60]}"
    return str(item)[:100]


def _candidate_list(results: list) -> str:
    if not results:
        return '未命中'
    lines = []
    for i, item in enumerate(results, 1):
        badge = chr(9989) if item.get('verified') else chr(9888)
        if item.get('style_of_cause'):
            lines.append(f"{i}. {badge} {item['style_of_cause']} —  {item.get('neutral_citation','')}")
        elif item.get('statute_title'):
            lines.append(f"{i}. {badge} {item['statute_title']} —  {item.get('neutral_citation','')}")
        elif item.get('name'):
            lines.append(f"{i}. {badge} {item['name']} —  {item.get('neutral_citation','')}")
        else:
            lines.append(f"{i}. {str(item)[:60]}")
    return chr(10).join(lines)


def format_result(item: dict) -> str:
    """将单条结果格式化为可读文本。"""
    lines = []

    if item.get("style_of_cause"):
        lines.append(f"案件名称: {item['style_of_cause']}")
        if item.get("neutral_citation"):
            lines.append(f"中立引用: {item['neutral_citation']}")
        if item.get("reporter") and item["reporter"] != item.get("neutral_citation"):
            lines.append(f"法库引用: {item['reporter']}")
        if item.get("year"):
            lines.append(f"年份: {item['year']}")
        if item.get("date"):
            lines.append(f"日期: {item['date']}")

    elif item.get("statute_title"):
        lines.append(f"法条名称: {item['statute_title']}")
        if item.get("neutral_citation"):
            lines.append(f"引用号: {item['neutral_citation']}")
        if item.get("jurisdiction"):
            lines.append(f"管辖区: {item['jurisdiction']}")

    elif item.get("name"):
        lines.append(f"名称: {item['name']}")
        if item.get("neutral_citation"):
            lines.append(f"引用号: {item['neutral_citation']}")

    if item.get("role"):
        lines.append(f"角色: {item['role']}")

    if item.get("url"):
        lines.append(f"链接: {item['url']}")

    if item.get("verified"):
        lines.append("✓ 已验证")
    if item.get("warning"):
        lines.append(item["warning"])

    return "\n".join(lines) if lines else str(item)


def tab1_search(query: str) -> tuple:
    """Step 1: 搜索。返回 8 值: radio, citation, state, 5 debug."""
    if timing.ENABLE_TIMING:
        timing.start()

    if not query or not query.strip():
        return (gr.update(choices=[], value=None), "请输入案例名、法条、或法律概念", [],
                "N/A", "N/A", "N/A", "N/A", "N/A")

    t0 = time.time()
    classified = classify_and_normalize(query.strip())
    if timing.ENABLE_TIMING:
        timing.report().set_classify(time.time() - t0)

    input_type = classified["type"]
    results = search_citation(query.strip(), classification=classified)

    WAIT = "等待选择候选..."
    route_debug = f"type={classified['type']}  normalized={classified['normalized']}"
    a2aj_debug = _candidate_list(results) if results else "未命中"

    if not results:
        return (gr.update(choices=[], value=None), "未找到匹配结果，请尝试其他关键词。", [],
                route_debug, a2aj_debug, "N/A", "N/A", "N/A")

    # bill → LEGISinfo 确定性路径，不过 A2AJ/detect_type/LLM
    if input_type == "bill":
        if not results[0].get("verified"):
            return (gr.update(choices=[], value=None),
                    f"⚠️ {results[0].get('warning', '未能在 LEGISinfo 验证该法案')}", [],
                    route_debug, "N/A（bill 路由不走 A2AJ）", "N/A", "N/A", "N/A")
        try:
            t0 = time.time()
            citation = format_citation(results[0])
            dbg = get_last_debug()
            if timing.ENABLE_TIMING:
                timing.report().set_format(time.time() - t0)
                timing.report().print()
            raw_r = (dbg.get("raw_response", "") or "")
            return (gr.update(choices=[], value=None), citation, [],
                    route_debug, "N/A（bill 路由不走 A2AJ）", "N/A（bill 路由不走 detect_type）",
                    "N/A（build_bill_citation 不过 LLM）", raw_r)
        except Exception as e:
            if timing.ENABLE_TIMING:
                timing.report().print()
            return (gr.update(choices=[], value=None), f"生成引用失败: {e}", [],
                    route_debug, "N/A", "N/A", "N/A", "N/A")

    # citation_number / legislation → 直接输出 McGill 引用
    if input_type in ("citation_number", "legislation"):
        try:
            t0 = time.time()
            citation = format_citation(results[0])
            dbg = get_last_debug()
            if timing.ENABLE_TIMING:
                timing.report().set_format(time.time() - t0)
                timing.report().print()
            dtype = detect_type(results[0])
            dtype_debug = f"detect_type={dtype}  rules={get_rules(dtype).get('category','?')}"
            prompt = (dbg.get("prompt", "") or "")[:500]
            raw_r = (dbg.get("raw_response", "") or "")
            return (gr.update(choices=[], value=None), citation, [],
                    route_debug, a2aj_debug, dtype_debug, prompt, raw_r)
        except Exception as e:
            if timing.ENABLE_TIMING:
                timing.report().print()
            return (gr.update(choices=[], value=None), f"生成引用失败: {e}", [],
                    route_debug, a2aj_debug, "N/A", "N/A", "N/A")

    # case_name / concept → 显示候选列表（引用尚未生成）
    candidates = []
    for i, item in enumerate(results, 1):
        badge = "✅" if item.get("verified") else "⚠️"
        if item.get("style_of_cause"):
            display = f"{i}. {badge} {item['style_of_cause']} — {item.get('neutral_citation', '')}"
        elif item.get("statute_title"):
            display = f"{i}. {badge} {item['statute_title']} — {item.get('neutral_citation', '')}"
        elif item.get("name"):
            display = f"{i}. {badge} {item['name']} — {item.get('neutral_citation', '')}"
        else:
            display = f"{i}. {badge} {item}"
        candidates.append(display)

    if timing.ENABLE_TIMING:
        timing.report().print()
    return (gr.update(choices=candidates, value=None), "请从上方候选列表中选择一条结果", results,
            route_debug, a2aj_debug, WAIT, WAIT, WAIT)


def tab1_select(choice: str, state: list) -> tuple:
    """Step 2: 用户选中候选后，按编号索引取出原始数据生成 McGill 引用。
    返回 (citation, route, a2aj, dtype, prompt_500, raw)."""
    if timing.ENABLE_TIMING:
        timing.start()

    if not choice:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip())
    if not state:
        return ("会话数据丢失，请重新搜索", "N/A", "N/A", "N/A", "N/A", "N/A")

    try:
        idx = int(choice.split(".")[0]) - 1
        if idx < 0 or idx >= len(state):
            return ("选中项索引超出范围", "N/A", "N/A", "N/A", "N/A", "N/A")
        item = state[idx]
        t0 = time.time()
        result = format_citation(item)
        if timing.ENABLE_TIMING:
            timing.report().set_format(time.time() - t0)
            timing.report().print()
        dbg = get_last_debug()
        prompt = (dbg.get("prompt", "") or "")[:500]
        raw_r = (dbg.get("raw_response", "") or "")
        dtype = detect_type(item)
        dtype_debug = f"detect_type={dtype}  rules={get_rules(dtype).get('category','?')}"
        full_list = _candidate_list(state)
        a2aj_debug = f"{full_list}\n\n>> 已选: {choice}"
        item_name = item.get("style_of_cause") or item.get("statute_title") or item.get("name", "?")
        route_debug = f">> selected #{idx+1}: {item_name}"
        return result, route_debug, a2aj_debug, dtype_debug, prompt, raw_r
    except (ValueError, IndexError, AttributeError, TypeError) as e:
        dbg = get_last_debug()
        p = (dbg.get("prompt", "") or "")[:500]
        r = (dbg.get("raw_response", "") or "")
        return (f"解析选中项失败: {e}", "N/A", "N/A", "N/A", p, r)


# ═══════════════════════════════════════════════
# Tab 2 — 文件提取
# ═══════════════════════════════════════════════

def tab2_extract(file) -> tuple:
    """返回 (citation, debug_info, meta, classify, crossref, source, prompt, raw)."""
    if file is None:
        return "请先上传文件", {}, "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"
    try:
        fields = extract_from_file(file.name)
        empty_fields = [k for k, v in fields.items() if not v]
        doc_type = classify_document_type(fields.get("raw_text", ""))
        citation = format_citation(fields, doc_type=doc_type)
        dbg = get_last_debug()
        debug_info = {
            "metadata": fields,
            "classified_type": doc_type,
            "source": dbg.get("source", ""),
            "empty_fields": empty_fields,
            "prompt": dbg.get("prompt", ""),
            "raw_response": dbg.get("raw_response", ""),
        }
        # Structured text fields
        title = fields.get("title", "")
        author = fields.get("author", "")
        date = fields.get("date", "")
        pub = fields.get("publisher", "")
        meta_str = f"title={title}  author={author}  date={date}  publisher={pub}"
        classify_str = f"doc_type={doc_type}"
        src = dbg.get("source", "")
        source_str = f"source={src}"
        prompt_500 = (dbg.get("prompt", "") or "")[:500]
        raw_r = dbg.get("raw_response", "") or ""
        if doc_type == "journal_article" and src == "crossref":
            crossref_str = "DOI 命中 → CrossRef 成功"
        elif doc_type == "journal_article" and src == "deepseek_fallback":
            crossref_str = "CrossRef 未命中（DOI 未找到或查询失败），降级 DeepSeek"
        else:
            crossref_str = f"N/A（非期刊路径: {doc_type}）"
        return citation, debug_info, meta_str, classify_str, crossref_str, source_str, prompt_500, raw_r
    except Exception as e:
        dbg = get_last_debug()
        debug_info = {"error": str(e)}
        if dbg.get("prompt"):
            debug_info["prompt"] = dbg["prompt"]
        if dbg.get("raw_response"):
            debug_info["raw_response"] = dbg["raw_response"]
        if dbg.get("source"):
            debug_info["source"] = dbg["source"]
        p = (dbg.get("prompt", "") or "")[:500]
        r = (dbg.get("raw_response", "") or "")
        s = dbg.get("source", "")
        return f"文件处理失败: {e}", debug_info, "N/A", "N/A", "N/A", f"source={s}", p, r


# ═══════════════════════════════════════════════
# Tab 3 — URL 提取
# ═══════════════════════════════════════════════

def tab3_url(url: str) -> tuple:
    """返回 (citation, debug_info, meta, dtype, source, prompt, raw)."""
    if not url or not url.strip():
        return "请输入URL", {}, "N/A", "N/A", "N/A", "N/A", "N/A"
    try:
        fields = extract_from_url(url.strip())
        if "error" in fields:
            return f"❌ {fields['error']}", {"metadata": fields}, "N/A", "N/A", "N/A", "N/A", "N/A"
        citation = format_citation(fields)
        dbg = get_last_debug()
        debug_info = {
            "metadata": fields,
            "empty_fields": [k for k, v in fields.items() if not v],
            "source": dbg.get("source", ""),
            "prompt": dbg.get("prompt", ""),
            "raw_response": dbg.get("raw_response", ""),
        }
        pt = fields.get("page_title", "") or ""
        au = fields.get("author", "") or ""
        dt = fields.get("date", "") or ""
        np = fields.get("newspaper", "") or ""
        hn = fields.get("hostname", "") or ""
        meta_str = f"url={url}  title={pt}  author={au}  date={dt}  newspaper={np}  hostname={hn}"
        dtype_str = f"detect_type={detect_type(fields)}"
        src = dbg.get("source", "")
        source_str = f"source={src}"
        prompt_500 = (dbg.get("prompt", "") or "")[:500]
        raw_r = dbg.get("raw_response", "") or ""
        return citation, debug_info, meta_str, dtype_str, source_str, prompt_500, raw_r
    except Exception as e:
        dbg = get_last_debug()
        debug_info = {"error": str(e)}
        if dbg.get("prompt"):
            debug_info["prompt"] = dbg["prompt"]
        if dbg.get("raw_response"):
            debug_info["raw_response"] = dbg["raw_response"]
        if dbg.get("source"):
            debug_info["source"] = dbg["source"]
        p = (dbg.get("prompt", "") or "")[:500]
        r = (dbg.get("raw_response", "") or "")
        s = dbg.get("source", "")
        return f"URL处理失败: {e}", debug_info, "N/A", "N/A", f"source={s}", p, r


# ═══════════════════════════════════════════════
# Tab 4 — 自由咨询（多轮对话）
# ═══════════════════════════════════════════════

def tab4_chat(message: str, history: list) -> tuple:
    """接收新消息 + 对话历史，返回(空, 更新后的历史)。"""
    if not message.strip():
        return "", history

    history = history or []
    history.append({"role": "user", "content": message.strip()})

    messages_for_api = []
    for turn in history:
        messages_for_api.append({"role": turn["role"], "content": turn["content"]})

    try:
        reply = chat_deepseek(messages_for_api)
        history.append({"role": "assistant", "content": reply})
    except Exception as e:
        history.append({"role": "assistant", "content": f"调用失败: {e}"})

    return "", history


# ═══════════════════════════════════════════════
# 构建 Gradio 界面
# ═══════════════════════════════════════════════

CSS = """
footer {display:none !important}
h1 { margin-bottom: 0.5em; }
"""

with gr.Blocks(title="McGill Citation Tool") as demo:
    gr.Markdown("# McGill Citation Tool")

    with gr.Tabs():
        # ─── Tab 1 ───────────────────────────────
        with gr.TabItem("🔍 Citation 查询"):
            with gr.Row():
                query_input = gr.Textbox(
                    label="查询",
                    placeholder="输入案例名、法条编号、或法律概念",
                    scale=4,
                )
            with gr.Row():
                submit_btn = gr.Button("Submit", variant="primary")

            candidates_radio = gr.Radio(
                label="候选结果（点击选择）",
                choices=[],
            )
            citation_md = gr.Markdown(label="McGill 引用")
            citation_plain = gr.Textbox(
                label="纯文本（复制用）", buttons=["copy"], lines=4
            )
            state_store = gr.State([])

            with gr.Accordion("🔍 Debug", open=False):
                t1_route  = gr.Textbox(label="分类路由", lines=2, interactive=False)
                t1_a2aj   = gr.Textbox(label="A2AJ 响应", lines=3, interactive=False)
                t1_dtype  = gr.Textbox(label="detect_type 判定", lines=1, interactive=False)
                t1_prompt = gr.Textbox(label="LLM Prompt（前 500 字）", lines=6, interactive=False)
                t1_raw    = gr.Textbox(label="LLM Raw Response", lines=6, interactive=False)

            t1_all = [candidates_radio, citation_md, state_store,
                      t1_route, t1_a2aj, t1_dtype, t1_prompt, t1_raw]

            # Step 1: Submit → 搜索，填充候选或直接输出
            submit_btn.click(
                fn=tab1_search, inputs=query_input, outputs=t1_all,
            ).success(
                fn=_plain_text, inputs=citation_md, outputs=citation_plain,
            )
            query_input.submit(
                fn=tab1_search, inputs=query_input, outputs=t1_all,
            ).success(
                fn=_plain_text, inputs=citation_md, outputs=citation_plain,
            )

            # Step 2: 点击候选 → 生成 McGill 引用
            candidates_radio.change(
                fn=tab1_select,
                inputs=[candidates_radio, state_store],
                outputs=[citation_md, t1_route, t1_a2aj, t1_dtype, t1_prompt, t1_raw],
            ).success(
                fn=_plain_text, inputs=citation_md, outputs=citation_plain,
            )

        # ─── Tab 2 ───────────────────────────────
        with gr.TabItem("📄 文件提取"):
            with gr.Row():
                file_input = gr.File(
                    label="上传文件",
                    file_types=[".docx", ".pdf", ".pptx", ".xlsx", ".xls"],
                )
            with gr.Row():
                file_submit = gr.Button("提取引用", variant="primary")
            file_md = gr.Markdown(label="McGill 引用")
            file_plain = gr.Textbox(
                label="纯文本（复制用）", buttons=["copy"], lines=4
            )
            with gr.Accordion("🔍 Debug", open=False):
                t2_meta     = gr.Textbox(label="文件解析结果", lines=2, interactive=False)
                t2_classify = gr.Textbox(label="LLM 文档分类", lines=1, interactive=False)
                t2_crossref = gr.Textbox(label="CrossRef 路径", lines=2, interactive=False)
                t2_source   = gr.Textbox(label="数据来源", lines=1, interactive=False)
                t2_prompt   = gr.Textbox(label="LLM Prompt（前 500 字）", lines=6, interactive=False)
                t2_raw      = gr.Textbox(label="LLM Raw Response", lines=6, interactive=False)
                file_debug  = gr.JSON(label="原始调试数据")

            t2_all = [file_md, file_debug, t2_meta, t2_classify, t2_crossref, t2_source, t2_prompt, t2_raw]

            file_submit.click(
                fn=tab2_extract, inputs=file_input, outputs=t2_all,
            ).success(
                fn=_plain_text, inputs=file_md, outputs=file_plain,
            )

        # ─── Tab 3 ───────────────────────────────
        with gr.TabItem("🌐 URL 提取"):
            with gr.Row():
                url_input = gr.Textbox(
                    label="URL",
                    placeholder="输入URL",
                    scale=4,
                )
            with gr.Row():
                url_submit = gr.Button("提取引用", variant="primary")
            url_md = gr.Markdown(label="McGill 引用")
            url_plain = gr.Textbox(
                label="纯文本（复制用）", buttons=["copy"], lines=4
            )
            with gr.Accordion("🔍 Debug", open=False):
                t3_meta   = gr.Textbox(label="trafilatura 提取结果", lines=3, interactive=False)
                t3_dtype  = gr.Textbox(label="detect_type 判定", lines=1, interactive=False)
                t3_source = gr.Textbox(label="数据来源", lines=1, interactive=False)
                t3_prompt = gr.Textbox(label="LLM Prompt（前 500 字）", lines=6, interactive=False)
                t3_raw    = gr.Textbox(label="LLM Raw Response", lines=6, interactive=False)
                url_debug = gr.JSON(label="原始调试数据")

            t3_all = [url_md, url_debug, t3_meta, t3_dtype, t3_source, t3_prompt, t3_raw]

            url_submit.click(
                fn=tab3_url, inputs=url_input, outputs=t3_all,
            ).success(
                fn=_plain_text, inputs=url_md, outputs=url_plain,
            )
            url_input.submit(
                fn=tab3_url, inputs=url_input, outputs=t3_all,
            ).success(
                fn=_plain_text, inputs=url_md, outputs=url_plain,
            )

        # ─── Tab 4 ───────────────────────────────
        with gr.TabItem("💬 自由咨询"):
            chatbot = gr.Chatbot(
                label="对话",
                height=400,
            )
            with gr.Row():
                msg_input = gr.Textbox(
                    label="输入",
                    placeholder="输入你的问题",
                    scale=4,
                )
                send_btn = gr.Button("发送", variant="primary", scale=1)

            send_btn.click(
                fn=tab4_chat,
                inputs=[msg_input, chatbot],
                outputs=[msg_input, chatbot],
            )
            msg_input.submit(
                fn=tab4_chat,
                inputs=[msg_input, chatbot],
                outputs=[msg_input, chatbot],
            )


if __name__ == "__main__":
    demo.launch(server_name="localhost", server_port=7860, share=True, css=CSS, theme=gr.themes.Soft())
