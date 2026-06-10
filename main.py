import sys
from local_tools.citation_search import search_citation, classify_and_normalize
from local_tools.file_extractor import extract_from_file
from llm_api.deepseek_api import extract_from_url, chat_deepseek
from core.mcgill_engine import format_citation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ═══════════════════════════════════════════════
# 功能 1 — Citation 查询
# ═══════════════════════════════════════════════

def citation_query_mode():
    """两步交互：搜索 -> 候选列表 -> 选点 -> 输出 McGill 引用。"""
    query = input("\n输入案例名、法条编号、或法律概念: ").strip()
    if not query:
        return

    # 分类路由
    classified = classify_and_normalize(query)
    input_type = classified["type"]
    results = search_citation(query)

    if not results:
        print("未找到匹配结果，请尝试其他关键词。")
        return

    # citation_number / legislation -> 直接输出
    if input_type in ("citation_number", "legislation"):
        try:
            citation = format_citation(results[0])
            if results[0].get("warning"):
                print(f"\n{results[0]['warning']}")
            print(f"\nMcGill 引用：{citation}")
        except Exception as e:
            print(f"生成引用失败: {e}")
        return

    # case_name / concept -> 候选列表
    print(f"\n找到 {len(results)} 条相关结果：")
    for i, item in enumerate(results, 1):
        name = item.get("style_of_cause") or item.get("statute_title") or item.get("name", "未知")
        cit = item.get("neutral_citation") or item.get("reporter", "")
        verified = "+" if item.get("verified") else "?"
        print(f"  {i}. {verified} {name} - {cit}")

    pick = input("\n选择编号（直接回车取第 1 条）: ").strip()
    idx = (int(pick) - 1) if pick.isdigit() else 0
    idx = max(0, min(idx, len(results) - 1))

    selected = results[idx]
    if selected.get("warning"):
        print(f"\n{selected['warning']}")
    try:
        print(f"\nMcGill 引用：{format_citation(selected)}")
    except Exception as e:
        print(f"生成引用失败: {e}")


# ═══════════════════════════════════════════════
# 功能 2 — 文件提取
# ═══════════════════════════════════════════════

def file_extract_mode():
    file_path = input("\n输入文件完整路径: ").strip()
    if not file_path:
        return
    try:
        fields = extract_from_file(file_path)
        citation = format_citation(fields)
        print(f"\nMcGill 引用：{citation}")
    except Exception as e:
        print(f"文件处理失败: {e}")


# ═══════════════════════════════════════════════
# 功能 3 — URL 提取
# ═══════════════════════════════════════════════

def url_extract_mode():
    url = input("\n输入 URL: ").strip()
    if not url:
        return
    try:
        fields = extract_from_url(url)
        if "error" in fields:
            print(f"Error: {fields['error']}")
            return
        citation = format_citation(fields)
        print(f"\nMcGill 引用：{citation}")
    except Exception as e:
        print(f"URL 处理失败: {e}")


# ═══════════════════════════════════════════════
# 功能 4 — 自由咨询
# ═══════════════════════════════════════════════

def chat_mode():
    print("\nDeepSeek 自由咨询（输入 exit 退出咨询）")
    history = []

    while True:
        user_input = input("你：").strip()
        if user_input.lower() == "exit":
            break
        if not user_input:
            continue
        history.append({"role": "user", "content": user_input})
        reply = chat_deepseek(history)
        history.append({"role": "assistant", "content": reply})
        print(f"DeepSeek: {reply}")


# ═══════════════════════════════════════════════
# 主菜单
# ═══════════════════════════════════════════════

def main():
    print("McGill Citation Tool")
    print("--------------------")

    while True:
        print("\n  1 = [1/4] Citation 查询")
        print("  2 = [2/4] 文件提取")
        print("  3 = [3/4] URL 提取")
        print("  4 = [4/4] 自由咨询")
        print("  0 = 退出")

        choice = input("\n选择功能: ").strip()

        if choice == "1":
            citation_query_mode()
        elif choice == "2":
            file_extract_mode()
        elif choice == "3":
            url_extract_mode()
        elif choice == "4":
            chat_mode()
        elif choice == "0":
            print("再见。")
            break


if __name__ == "__main__":
    main()
