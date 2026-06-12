"""Profile runner: exercise all 5 query paths, collect timing, output tables.

Usage:
    cd C:/Users/hp/Desktop/mcgill
    .venv_new/Scripts/python.exe -m profiling.run
"""

import sys, os, json, time as std_time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from profiling import timing as prof
from local_tools.citation_search import search_citation
from core.mcgill_engine import format_citation


# ── 测试用例 ──────────────────────────────────────────

CASES = [
    {
        "name": "concept",
        "desc": "gladue principle -> expand_concept",
        "fn": lambda: search_citation("gladue principle"),
    },
    {
        "name": "journal_crossref",
        "desc": "Flavonoid DOI -> CrossRef -> build_journal_citation",
        "fn": lambda: format_citation(
            {"title": "Flavonoid B-Ring Chemistry",
             "author": "Ananth Sekher Pannala",
             "raw_text": "doi:10.1006/bbrc.2001.4705 Biochem Biophys Res Commun"},
            doc_type="journal_article",
        ),
    },
    {
        "name": "citation_number",
        "desc": "2022 SCC 39 -> A2AJ /fetch",
        "fn": lambda: search_citation("2022 SCC 39"),
    },
    {
        "name": "case_name",
        "desc": "R v Askov 1990 -> A2AJ /\bsearch multi-batch",
        "fn": lambda: search_citation("R v Askov 1990"),
    },
    {
        "name": "legislation",
        "desc": "Criminal Code -> A2AJ /fetch(doc_type=laws)",
        "fn": lambda: search_citation("Criminal Code"),
    },
]

RUNS_PER_CASE = 3


# ── 辅助函数 ──────────────────────────────────────────

def _fmt(ms):
    return f"{ms:.1f}"

def _type(label):
    if label.startswith("llm."):
        return "LLM"
    if label.startswith("http."):
        return "HTTP"
    return "OTHER"

def _endpoint(rec):
    return rec["meta"].get("endpoint", "") or rec["meta"].get("model", "") or ""


def compute_overlaps(records):
    """Return a set of indices that overlap with at least one other span."""
    n = len(records)
    overlapping = set()
    for i in range(n):
        si, ei = records[i]["start_s"], records[i]["end_s"]
        for j in range(n):
            if i == j:
                continue
            sj, ej = records[j]["start_s"], records[j]["end_s"]
            if si < ej and sj < ei:
                overlapping.add(i)
                break
    return overlapping


def median_durations(runs):
    """Aggregate records across runs: for each label, take median duration."""
    by_label = {}
    for records in runs:
        for rec in records:
            lbl = rec["label"]
            by_label.setdefault(lbl, []).append(rec["duration_s"])
    result = {}
    for lbl, durations in by_label.items():
        durations.sort()
        result[lbl] = durations[len(durations) // 3]  # median of 3
    return result


# ── 单次运行 ──────────────────────────────────────────

def run_single(case_fn):
    prof.reset()
    t0 = std_time.perf_counter()
    result = case_fn()
    wall = std_time.perf_counter() - t0
    records = prof.dump()
    return wall, records, result


# ── 表格打印 ──────────────────────────────────────────

def print_table(case, wall, records, overlapping):
    print(f"\n{'='*72}")
    print(f"  {case['name']:25s}  wall={_fmt(wall*1000)}ms  spans={len(records)}")
    print(f"  {' '*25}  {case['desc']}")
    print(f"{'='*72}")
    if not records:
        print("  (no timing records collected)")
        return

    header = f"{'stage':30s} {'start(ms)':>10s} {'dur(ms)':>10s} {'type':6s} {'endpoint':20s} {'parallel?':8s}"
    print(header)
    print("-" * len(header))
    base = records[0]["start_s"]
    for i, rec in enumerate(records):
        start_ms = (rec["start_s"] - base) * 1000
        dur_ms = rec["duration_s"] * 1000
        lbl = rec["label"]
        typ = _type(lbl)
        ep = _endpoint(rec)[:20]
        par = "OVERLAP" if i in overlapping else "serial"
        print(f"{lbl:30s} {start_ms:>10.1f} {dur_ms:>10.1f} {typ:6s} {ep:20s} {par:8s}")


def print_summary(cases_results):
    print(f"\n{'='*72}")
    print("  SUMMARY")
    print(f"{'='*72}")
    all_llm = []
    all_http = []
    all_walls = []
    for name, wall, records in cases_results:
        llm_sum = sum(r["duration_s"] for r in records if r["label"].startswith("llm."))
        http_sum = sum(r["duration_s"] for r in records if r["label"].startswith("http."))
        all_llm.append(llm_sum)
        all_http.append(http_sum)
        all_walls.append(wall)
        print(f"  {name:25s}  wall={_fmt(wall*1000):>6s}ms  LLM={_fmt(llm_sum*1000):>6s}ms  HTTP={_fmt(http_sum*1000):>6s}ms")
    print()
    # Find max single segment across all runs
    all_segments = []
    for _, _, records in cases_results:
        all_segments.extend(records)
    max_seg = max(all_segments, key=lambda r: r["duration_s"]) if all_segments else None
    if max_seg:
        print(f"  Max single segment: {max_seg['label']} @ {_fmt(max_seg['duration_s']*1000)}ms")


# ── 主程序 ──────────────────────────────────────────

def main():
    results_for_json = {}
    cases_results = []

    for case in CASES:
        print(f"\n>>> Running {case['name']} ({case['desc']})")
        runs = []
        for rn in range(1, RUNS_PER_CASE + 1):
            try:
                wall, records, result = run_single(case["fn"])
                runs.append((wall, records))
                print(f"    run {rn}: wall={_fmt(wall*1000)}ms, {len(records)} spans")
            except Exception as e:
                print(f"    run {rn}: ERROR {e}")
                runs.append((0, []))

        # Use median wall time run for display
        runs_sorted = sorted(runs, key=lambda x: x[0])
        median_idx = len(runs_sorted) // 2
        median_wall, median_records = runs_sorted[median_idx]

        overlapping = compute_overlaps(median_records)
        print_table(case, median_wall, median_records, overlapping)
        cases_results.append((case["name"], median_wall, median_records))
        results_for_json[case["name"]] = {
            "wall_s": round(median_wall, 3),
            "spans": [
                {"label": r["label"], "start_s": round(r["start_s"], 4),
                 "end_s": round(r["end_s"], 4), "duration_s": round(r["duration_s"], 3),
                 "meta": r["meta"]}
                for r in median_records
            ],
        }
        std_time.sleep(1)  # cool-down between cases

    print_summary(cases_results)

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "profile_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results_for_json, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {json_path}")


if __name__ == "__main__":
    main()
