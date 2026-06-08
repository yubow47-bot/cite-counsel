import sys
from core.model_router import route
from local_tools.citation_tracker import CitationTracker
from llm_api.deepseek_api import chat_deepseek

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def local_mode():
    tracker = CitationTracker()

    while True:
        print("\n本地模式")
        print("  1 = Citation 查询(A2AJ)")
        print("  2 = 文件提取(docx / pdf / pptx / xlsx)")
        print("  3 = ibid / supra 管理")
        print("  4 = DeepSeek 自由咨询")

        choice = input("选择功能: ").strip()

        if choice == "1":
            from local_tools.citation_search import search_citation
            from core.mcgill_engine import format_citation

            query = input("输入 citation、案件名、法条或法律概念: ").strip()
            results = search_citation(query)

            if not results:
                print("未找到相关结果，请尝试其他关键词。")
                continue

            if len(results) == 1:
                r = results[0]
                if r.get("warning"):
                    print(f"\n{r['warning']}")
                if "style_of_cause" in r or "statute_title" in r:
                    print(f"\nMcGill 引用：{format_citation(r)}")
                continue

            print(f"\n找到 {len(results)} 条相关结果：")
            for i, r in enumerate(results, 1):
                name = r.get("style_of_cause") or r.get("statute_title") or r.get("name", "未知")
                citation = r.get("neutral_citation") or r.get("reporter", "")
                verified = "✅" if r.get("verified") else "⚠️"
                print(f"  {i}. {verified} {name} — {citation}")

            pick = input("\n选择编号(直接回车取第1条): ").strip()
            idx = (int(pick) - 1) if pick.isdigit() else 0
            idx = max(0, min(idx, len(results) - 1))

            selected = results[idx]
            if selected.get("warning"):
                print(f"\n{selected['warning']}")
            print(f"\nMcGill 引用：{format_citation(selected)}")


        elif choice == "2":
            file_path = input("输入文件完整路径: ").strip()
            result = route("file", file_path)
            print(f"\nMcGill 引用：{result}")

        elif choice == "3":
            ibid_menu(tracker)

        elif choice == "4":
            chat_mode()


def ibid_menu(tracker: CitationTracker):
    while True:
        print("\nibid / supra 管理")
        print("  1 = 添加引用记录")
        print("  2 = 生成 ibid / supra")
        print("  3 = 查看已记录引用")
        print("  4 = 新建项目（清空当前记录）")

        choice = input("选择: ").strip()
        if choice == "1":
            num = int(input("脚注编号: ").strip())
            full = input("完整引用: ").strip()
            short = input("短形式（留空自动生成）: ").strip()
            tracker.add_citation(num, full, short)
            print(f"已记录脚注 {num}。")

        
        elif choice == "2":
            current = int(input("当前脚注编号: ").strip())
            target = int(input("目标脚注编号: ").strip())
            pinpoint = input("页码/段落号（可选，留空跳过）: ").strip()
            import json
            content = json.dumps({
                "footnote_num": current,
                "target": target,
                "pinpoint": pinpoint
            })
            result = route("ibid", content, tracker=tracker)
            print(f"\n引用:{result}")

        elif choice == "3":
            print("\n已记录引用:")
            print(tracker.show_history())

        elif choice == "4":
            confirm = input("确认新建项目?当前记录将清空(y/n): ").strip().lower()
            if confirm == "y":
                tracker.new_project()
                print("已新建项目，引用记录已清空。")


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
        print(f"DeepSeek:{reply}")


def main():
    print("McGill Citation Tool")
    print("--------------------")

    while True:
        print("\n  1 = 本地模式")
        print("  2 = 联网模式(DeepSeek)")

        mode = input("选择模式: ").strip()

        if mode == "1":
            local_mode()

        elif mode == "2":
            content = input("\n输入链接: ").strip()
            print("  正在分析链接...", flush=True)
            result = route("llm", content)
            print(f"\nMcGill 引用：{result}")


if __name__ == "__main__":
    main()