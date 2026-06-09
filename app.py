"""McGill Citation Tool — Gradio 前端（四Tab版）"""

import gradio as gr
from local_tools.citation_search import search_citation
from local_tools.file_extractor import extract_from_file
from llm_api.deepseek_api import extract_from_url, chat_deepseek
from core.mcgill_engine import format_citation


# ═══════════════════════════════════════════════
# Tab 1 — Citation 查询
# ═══════════════════════════════════════════════

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


def tab1_lookup(query: str) -> str:
    if not query or not query.strip():
        return "请输入案例名、法条、或法律概念"
    results = search_citation(query.strip())
    if not results:
        return "未找到匹配结果，请尝试其他关键词。"
    outputs = []
    for i, item in enumerate(results, 1):
        if len(results) > 1:
            outputs.append(f"--- 结果 {i} ---")
        outputs.append(format_result(item))
        outputs.append("")
    return "\n".join(outputs).strip()


# ═══════════════════════════════════════════════
# Tab 2 — 文件提取
# ═══════════════════════════════════════════════

def tab2_extract(file) -> str:
    if file is None:
        return "请先上传文件"
    try:
        fields = extract_from_file(file.name)
        citation = format_citation(fields)
        return citation
    except Exception as e:
        return f"文件处理失败: {e}"


# ═══════════════════════════════════════════════
# Tab 3 — URL 提取
# ═══════════════════════════════════════════════

def tab3_url(url: str) -> str:
    if not url or not url.strip():
        return "请输入URL"
    try:
        fields = extract_from_url(url.strip())
        if "error" in fields:
            return f"❌ {fields['error']}"
        citation = format_citation(fields)
        return citation
    except Exception as e:
        return f"URL处理失败: {e}"


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
            output = gr.Textbox(label="结果", lines=12)

            submit_btn.click(fn=tab1_lookup, inputs=query_input, outputs=output)
            query_input.submit(fn=tab1_lookup, inputs=query_input, outputs=output)

        # ─── Tab 2 ───────────────────────────────
        with gr.TabItem("📄 文件提取"):
            with gr.Row():
                file_input = gr.File(
                    label="上传文件",
                    file_types=[".docx", ".pdf", ".pptx", ".xlsx", ".xls"],
                )
            with gr.Row():
                file_submit = gr.Button("提取引用", variant="primary")
            file_output = gr.Textbox(label="McGill 引用", lines=12)

            file_submit.click(fn=tab2_extract, inputs=file_input, outputs=file_output)

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
            url_output = gr.Textbox(label="McGill 引用", lines=12)

            url_submit.click(fn=tab3_url, inputs=url_input, outputs=url_output)
            url_input.submit(fn=tab3_url, inputs=url_input, outputs=url_output)

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
    demo.launch(server_name="localhost", server_port=7860, css=CSS, theme=gr.themes.Soft())
