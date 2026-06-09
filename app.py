"""McGill Citation Tool - Gradio 前端"""

import gradio as gr
from local_tools.citation_search import search_citation


def format_result(item: dict) -> str:
    """将单条结果格式化为可读文本。"""
    lines = []

    # 判例
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

    # 法条
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

    # role (concept 展开时)
    if item.get("role"):
        lines.append(f"角色: {item['role']}")

    # URL
    if item.get("url"):
        lines.append(f"链接: {item['url']}")

    # 验证状态
    if item.get("verified"):
        lines.append("✓ 已验证")
    if item.get("warning"):
        lines.append(item["warning"])

    return "\n".join(lines) if lines else str(item)


def lookup_citation(query: str) -> str:
    """处理用户输入并返回格式化结果。"""
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


# 构建 Gradio 界面
with gr.Blocks(title="McGill Citation Tool") as demo:
    gr.Markdown("# McGill Citation Tool")
    gr.Markdown("输入案例名、法条编号、或法律概念，查询对应的 McGill 格式引用。")

    with gr.Row():
        query_input = gr.Textbox(
            label="查询",
            placeholder="输入案例名、法条、或法律概念",
            scale=3,
        )

    with gr.Row():
        submit_btn = gr.Button("Submit", variant="primary")

    output = gr.Textbox(label="结果", lines=12)

    submit_btn.click(
        fn=lookup_citation,
        inputs=query_input,
        outputs=output,
    )
    query_input.submit(
        fn=lookup_citation,
        inputs=query_input,
        outputs=output,
    )


if __name__ == "__main__":
    demo.launch(server_name="localhost", server_port=7860)
