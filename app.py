"""McGill Citation Tool — Gradio 前端（四Tab版）"""

import time
import gradio as gr
from local_tools.citation_search import search_citation, classify_and_normalize
from local_tools.file_extractor import extract_from_file, classify_document_type
from local_tools import timing_util as timing
from llm_api.deepseek_api import extract_from_url, chat_deepseek
from core.mcgill_engine import format_citation, get_last_debug


# ═══════════════════════════════════════════════
# Tab 1 — Citation 查询
# ═══════════════════════════════════════════════

def _plain_text(md_text: str) -> str:
    """去除 Markdown 标记（*），供纯文本复制用。"""
    if not md_text:
        return ""
    return md_text.replace("*", "")


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
    """Step 1: 搜索。返回 (候选列表更新, 输出文本, 原始数据列表)。"""
    if timing.ENABLE_TIMING:
        timing.start()

    if not query or not query.strip():
        return gr.update(choices=[], value=None), "请输入案例名、法条、或法律概念", []

    t0 = time.time()
    classified = classify_and_normalize(query.strip())
    if timing.ENABLE_TIMING:
        timing.report().set_classify(time.time() - t0)

    input_type = classified["type"]
    results = search_citation(query.strip(), classification=classified)

    if not results:
        return gr.update(choices=[], value=None), "未找到匹配结果，请尝试其他关键词。", []

    # citation_number / legislation → 直接输出 McGill 引用
    if input_type in ("citation_number", "legislation"):
        try:
            t0 = time.time()
            citation = format_citation(results[0])
            if timing.ENABLE_TIMING:
                timing.report().set_format(time.time() - t0)
                timing.report().print()
            return gr.update(choices=[], value=None), citation, []
        except Exception as e:
            if timing.ENABLE_TIMING:
                timing.report().print()
            return gr.update(choices=[], value=None), f"生成引用失败: {e}", []

    # case_name / concept → 显示带编号的候选列表（含验证状态）
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
    return gr.update(choices=candidates, value=None), "请从上方候选列表中选择一条结果", results


def tab1_select(choice: str, state: list) -> str:
    """Step 2: 用户选中候选后，按编号索引取出原始数据生成 McGill 引用。"""
    if timing.ENABLE_TIMING:
        timing.start()

    if not choice:
        return gr.skip()  # Radio 被程序清空时不修改输出
    if not state:
        return "会话数据丢失，请重新搜索"

    try:
        idx = int(choice.split(".")[0]) - 1
        if idx < 0 or idx >= len(state):
            return "选中项索引超出范围"
        item = state[idx]
        t0 = time.time()
        result = format_citation(item)
        if timing.ENABLE_TIMING:
            timing.report().set_format(time.time() - t0)
            timing.report().print()
        return result
    except (ValueError, IndexError, AttributeError, TypeError) as e:
        return f"解析选中项失败: {e}"


# ═══════════════════════════════════════════════
# Tab 2 — 文件提取
# ═══════════════════════════════════════════════

def tab2_extract(file) -> tuple:
    if file is None:
        return "请先上传文件", {}
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
        return citation, debug_info
    except Exception as e:
        dbg = get_last_debug()
        debug_info = {"error": str(e)}
        if dbg.get("prompt"):
            debug_info["prompt"] = dbg["prompt"]
        if dbg.get("raw_response"):
            debug_info["raw_response"] = dbg["raw_response"]
        if dbg.get("source"):
            debug_info["source"] = dbg["source"]
        return f"文件处理失败: {e}", debug_info


# ═══════════════════════════════════════════════
# Tab 3 — URL 提取
# ═══════════════════════════════════════════════

def tab3_url(url: str) -> tuple:
    if not url or not url.strip():
        return "请输入URL", {}
    try:
        fields = extract_from_url(url.strip())
        if "error" in fields:
            return f"❌ {fields['error']}", {"metadata": fields}
        citation = format_citation(fields)
        dbg = get_last_debug()
        debug_info = {
            "metadata": fields,
            "empty_fields": [k for k, v in fields.items() if not v],
            "source": dbg.get("source", ""),
            "prompt": dbg.get("prompt", ""),
            "raw_response": dbg.get("raw_response", ""),
        }
        return citation, debug_info
    except Exception as e:
        dbg = get_last_debug()
        debug_info = {"error": str(e)}
        if dbg.get("prompt"):
            debug_info["prompt"] = dbg["prompt"]
        if dbg.get("raw_response"):
            debug_info["raw_response"] = dbg["raw_response"]
        if dbg.get("source"):
            debug_info["source"] = dbg["source"]
        return f"URL处理失败: {e}", debug_info


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

            # Step 1: Submit → 搜索，填充候选或直接输出
            submit_btn.click(
                fn=tab1_search,
                inputs=query_input,
                outputs=[candidates_radio, citation_md, state_store],
            ).success(
                fn=_plain_text, inputs=citation_md, outputs=citation_plain,
            )
            query_input.submit(
                fn=tab1_search,
                inputs=query_input,
                outputs=[candidates_radio, citation_md, state_store],
            ).success(
                fn=_plain_text, inputs=citation_md, outputs=citation_plain,
            )

            # Step 2: 点击候选 → 生成 McGill 引用
            candidates_radio.change(
                fn=tab1_select,
                inputs=[candidates_radio, state_store],
                outputs=citation_md,
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
            with gr.Accordion("调试信息", open=False):
                file_debug = gr.JSON(label="调试详情")

            file_submit.click(
                fn=tab2_extract, inputs=file_input, outputs=[file_md, file_debug]
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
            with gr.Accordion("调试信息", open=False):
                url_debug = gr.JSON(label="调试详情")

            url_submit.click(
                fn=tab3_url, inputs=url_input, outputs=[url_md, url_debug]
            ).success(
                fn=_plain_text, inputs=url_md, outputs=url_plain,
            )
            url_input.submit(
                fn=tab3_url, inputs=url_input, outputs=[url_md, url_debug]
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
