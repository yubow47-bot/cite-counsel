"""Timing diagnostics for Citation query pipeline.

Usage:
    import timing_util as timing

    timing.start()               # at handler entry
    # ... do work, calling timing.report().add_llm(...) / add_a2aj(...) at boundaries
    timing.report().print()      # at handler exit
"""

import time

ENABLE_TIMING = True

_report = None


def start():
    global _report
    _report = TimingReport()


def report():
    global _report
    if _report is None and ENABLE_TIMING:
        _report = TimingReport()
    return _report


class TimingReport:
    def __init__(self):
        self.start_ts = time.time()
        self.classify_t = None          # 分类阶段总耗时
        self.a2aj_calls = []            # [(label, elapsed_seconds)]
        self.format_t = None            # 格式化阶段总耗时
        self.llm_calls = []             # [(label, elapsed_seconds)]

    def set_classify(self, elapsed):
        self.classify_t = elapsed

    def set_format(self, elapsed):
        self.format_t = elapsed

    def add_a2aj(self, label, elapsed):
        self.a2aj_calls.append((label, elapsed))

    def add_llm(self, label, elapsed):
        self.llm_calls.append((label, elapsed))

    @property
    def total(self):
        return time.time() - self.start_ts

    def print(self):
        if not ENABLE_TIMING:
            return
        a2aj_total = sum(t for _, t in self.a2aj_calls)
        a2aj_count = len(self.a2aj_calls)
        c = f"{self.classify_t:.1f}s" if self.classify_t is not None else "N/A"
        f = f"{self.format_t:.1f}s" if self.format_t is not None else "N/A"
        print("")
        print(f"[TIMING] 分类: {c} | A2AJ: {a2aj_total:.1f}s ({a2aj_count}次请求) | 格式化: {f} | 总计: {self.total:.1f}s")
        if self.llm_calls:
            print(f"  LLM调用 ({len(self.llm_calls)}次):")
            for i, (lbl, t) in enumerate(self.llm_calls, 1):
                print(f"    #{i} {lbl}: {t:.1f}s")
        if self.a2aj_calls:
            print(f"  A2AJ请求 ({a2aj_count}次):")
            for lbl, t in self.a2aj_calls:
                truncated = lbl if len(lbl) <= 70 else lbl[:67] + "..."
                print(f"    {truncated}: {t:.1f}s")
