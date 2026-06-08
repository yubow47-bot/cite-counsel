import json
import os

SESSION_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "current_session.json")


class CitationTracker:
    """追踪脚注引用历史，自动判断 ibid / supra。

    引用历史保存在 data/current_session.json,
    同一篇论文跨多次会话可继续使用，新建项目时手动清空。
    """

    def __init__(self):
        self.citations: list[dict] = []
        self.last_num: int = 0
        self._load_session()

    def _load_session(self):
        """启动时读取当前 session。"""
        if os.path.exists(SESSION_PATH):
            try:
                with open(SESSION_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.citations = data.get("citations", [])
                self.last_num = data.get("last_num", 0)
            except Exception:
                self.citations = []
                self.last_num = 0

    def _save_session(self):
        """每次修改后自动保存。"""
        os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
        with open(SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "citations": self.citations,
                "last_num": self.last_num
            }, f, ensure_ascii=False, indent=2)

    def new_project(self):
        """新建项目，清空当前 session。"""
        self.citations = []
        self.last_num = 0
        self._save_session()

    def add_citation(self, footnote_num: int, full_citation: str, short_form: str = ""):
        """记录一条新引用并保存。"""
        entry = {
            "num": footnote_num,
            "full": full_citation,
            "short": short_form or full_citation.split(",")[0].strip(),
        }
        # 替换或追加
        for i, c in enumerate(self.citations):
            if c["num"] == footnote_num:
                self.citations[i] = entry
                break
        else:
            self.citations.append(entry)
        self.last_num = footnote_num
        self._save_session()

    def auto_add(self, full_citation: str):
        """自动分配脚注编号并记录引用。"""
        self.last_num += 1
        short = full_citation.split(",")[0].strip()
        self.citations.append({
            "num": self.last_num,
            "full": full_citation,
            "short": short,
        })
        self._save_session()
        return self.last_num

    def get_reference(self, footnote_num: int, target_footnote_num: int, pinpoint: str = "") -> str:
        """
        根据目标脚注号自动返回 ibid 或 supra。
        footnote_num:        当前脚注编号
        target_footnote_num: 要引用的那条脚注编号
        pinpoint:            页码或段落号（可选）
        """
        target = None
        for c in self.citations:
            if c["num"] == target_footnote_num:
                target = c
                break

        if target is None:
            return f"[错误：脚注 {target_footnote_num} 未记录，请先用「添加引用记录」添加]"

        pinpoint_str = f" at {pinpoint}" if pinpoint else ""

        # 目标是最近添加的引用记录 → ibid
        if target_footnote_num == self.last_num:
            return f"Ibid{pinpoint_str}."

        # 目标是更早的引用记录 → supra
        short = target["short"]
        return f"{short}, supra note {target_footnote_num}{pinpoint_str}."

    def show_history(self) -> str:
        """显示当前 session 所有已记录的引用。"""
        if not self.citations:
            return "（当前项目暂无引用记录）"
        lines = []
        for entry in sorted(self.citations, key=lambda x: x["num"]):
            lines.append(f"  {entry['num']}. {entry['full']}  [short: {entry['short']}]")
        return "\n".join(lines)
