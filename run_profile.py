#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprehensive latency-profiling harness.

Instruments every meaningful boundary across all three tabs,
runs representative inputs, dumps RAW per-step timings, and
produces a SUMMARY TABLE at the end.

Usage:
    cd /path/to/mcgill
    python run_profile.py

Environment: uses real .env API keys for production-realistic timings.
"""

import os
import sys
import json
import time
import traceback
from pathlib import Path

# Force UTF-8 for stdout to avoid GBK encoding errors
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJ))

from profiling import timing as prof
from local_tools import timing_util as timing

# ── Results accumulator ──
results = []


def reset_profilers():
    prof.reset()
    timing.start()


def dump_profile(label="") -> dict:
    """Dump all profiling.timing records with human-readable output.
    Returns summary dict."""
    records = prof.dump()
    if not records:
        print("  [PROFILE] (no profiling records)")
        return {"records": [], "slowest": None, "retry_labels": []}

    print(f"  [PROFILE] Raw records ({len(records)}):")
    for r in records:
        meta_str = json.dumps(r["meta"]) if r.get("meta") else ""
        print(f"    {r['label']:50s}  {r['duration_s']*1000:9.1f}ms  {meta_str}")

    # Sort by duration desc
    sorted_r = sorted(records, key=lambda r: r["duration_s"], reverse=True)
    slowest = sorted_r[0] if sorted_r else None

    # Check for retries (same label appearing more than once)
    seen = {}
    for r in records:
        seen.setdefault(r["label"], []).append(r["duration_s"])
    retry_labels = {lbl for lbl, times in seen.items() if len(times) > 1}

    if slowest:
        print(f"  [PROFILE] Slowest step: {slowest['label']} ({slowest['duration_s']*1000:.0f}ms)")
    if retry_labels:
        print(f"  [PROFILE] RETRY detected (same label appears >1x): {retry_labels}")

    return {
        "records": records,
        "slowest": slowest,
        "retry_labels": retry_labels,
    }


def run_test(test_id: str, description: str, route: str, deterministic: bool,
             fn, timeout_s: int = 120):
    """Execute one test case and collect timing data."""
    print(f"\n{'#'*70}")
    print(f"# TEST #{test_id}: {description}")
    print(f"# route={route}, deterministic={deterministic}")
    print(f"{'#'*70}")

    reset_profilers()
    start_ts = time.perf_counter()
    status = "OK"
    detail = ""
    error_trace = ""

    try:
        result = fn()
        elapsed = time.perf_counter() - start_ts
        if isinstance(result, str):
            detail = result[:150]
        elif isinstance(result, dict):
            # Truncate long values
            truncated = {}
            for k, v in result.items():
                if isinstance(v, str) and len(v) > 100:
                    truncated[k] = v[:100] + "..."
                elif isinstance(v, list) and len(v) > 3:
                    truncated[k] = f"list[{len(v)}]: {json.dumps(v[:3], ensure_ascii=False)[:100]}"
                else:
                    truncated[k] = v
            detail = json.dumps(truncated, ensure_ascii=False)[:200]
        elif isinstance(result, list):
            detail = f"list[{len(result)} items]"
            if result:
                detail += f" first: {json.dumps(result[0], ensure_ascii=False)[:100]}"
        else:
            detail = str(result)[:200]
    except Exception as e:
        elapsed = time.perf_counter() - start_ts
        status = f"ERROR"
        detail = f"{type(e).__name__}: {e}"
        error_trace = traceback.format_exc()

    profile_summary = dump_profile(description)

    print(f"\n  [RESULT] status={status}, total={elapsed*1000:.0f}ms ({elapsed:.2f}s)")
    print(f"  [RESULT] detail={detail}")
    if error_trace:
        print(f"  [RESULT] traceback (last 5 lines):")
        for line in error_trace.strip().split("\n")[-5:]:
            print(f"    {line}")

    entry = {
        "test_id": test_id,
        "description": description,
        "route": route,
        "deterministic": deterministic,
        "total_ms": round(elapsed * 1000, 1),
        "status": status,
        "detail": detail,
        "profile": profile_summary,
    }
    results.append(entry)
    return entry


# ═══════════════════════════════════════════════════════════════════
#  Import all modules needed
# ═══════════════════════════════════════════════════════════════════
from local_tools.citation_search import classify_and_normalize, search_citation
from local_tools.file_extractor import extract_from_file, classify_document_type
from llm_api.deepseek_api import extract_from_url
from core.mcgill_engine import format_citation


# ═══════════════════════════════════════════════════════════════════
#  TAB 1 — Citation Search
# ═══════════════════════════════════════════════════════════════════

def tab1_citation_query(query: str) -> dict:
    """Full Tab1 pipeline: classify -> search -> format (single result)."""
    classified = classify_and_normalize(query)
    route = classified["type"]
    results = search_citation(query, classification=classified)

    if not results:
        return {"route": route, "action": "no_results", "count": 0}

    # If multiple results, just return the first for format timing
    target = results[0]

    # Bills are assembled deterministically
    if target.get("_bill_citation"):
        citation = format_citation(target)
        return {"route": route, "action": "done", "deterministic": True,
                "citation_preview": citation[:100]}

    # Everything else goes through LLM formatting
    citation = format_citation(target)
    return {"route": route, "action": "done", "deterministic": False,
            "citation_preview": citation[:100]}


def tab1_citation_query_search_only(query: str) -> dict:
    """Tab1 pipeline: classify -> search (stop before format, for multi-result routes)."""
    classified = classify_and_normalize(query)
    route = classified["type"]
    results = search_citation(query, classification=classified)
    return {"route": route, "count": len(results) if results else 0,
            "results_preview": str(results)[:200] if results else "[]"}


# ══════════════════════════════════════════════════════════════════
#  TAB 3 — URL / DOI / ISBN path helpers
# ══════════════════════════════════════════════════════════════════

def tab3_journal_by_doi(doi: str) -> dict:
    """Tab3 DOI path: format_citation with doc_type=journal_article -> CrossRef."""
    fields = {"raw_text": doi, "url": ""}
    citation = format_citation(fields, doc_type="journal_article")
    return {"route": "url/doi", "citation_preview": citation[:150]}


def tab3_book_by_isbn(isbn: str) -> dict:
    """Tab3 ISBN path: format_citation with doc_type=book -> Open Library."""
    fields = {"raw_text": isbn}
    citation = format_citation(fields, doc_type="book")
    return {"route": "url/isbn", "citation_preview": citation[:150]}


def tab3_url_extract(url: str) -> dict:
    """Tab3 URL path: extract_from_url -> classify_document_type -> format_citation."""
    fields = extract_from_url(url)
    if "error" in fields:
        return {"route": "url", "action": "unsupported", "error": fields.get("error")}
    raw_text = fields.get("raw_text", "") or ""
    doc_type = "website"
    if len(raw_text.strip()) >= 50:
        doc_type = classify_document_type(raw_text)
    citation = format_citation(fields, doc_type=doc_type)
    return {"route": "url", "action": "done", "doc_type": doc_type,
            "citation_preview": citation[:150]}


def tab3_url_unsupported(url: str) -> dict:
    """Tab3 URL path for JS-rendered pages — just returns error timing."""
    fields = extract_from_url(url)
    return {"route": "url/js_rendered", "error": fields.get("error", "no error"),
            "has_raw_text": bool(fields.get("raw_text", "").strip())}


# ══════════════════════════════════════════════════════════════════
#  RUN ALL TESTS
# ══════════════════════════════════════════════════════════════════

print("=" * 70)
print("  MCGILL CITATION TOOL — FULL LATENCY PROFILING")
print(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ── Tab1: Citation Search ─────────────────────────────────────

# Test 1: "r v leo at para 2" — case name search (3min offender)
run_test("1", "r v leo at para 2 — case name search (3min suspect)",
         "case_name", False,
         lambda: tab1_citation_query_search_only("r v leo at para 2"),
         timeout_s=300)

# Test 2: "R v Oakes" — clean case name
run_test("2", "R v Oakes — clean case name search",
         "case_name", False,
         lambda: tab1_citation_query("R v Oakes"),
         timeout_s=120)

# Test 3: "2022 SCC 39" — neutral citation
run_test("3", "2022 SCC 39 — neutral citation number",
         "citation_number", False,
         lambda: tab1_citation_query("2022 SCC 39"),
         timeout_s=60)

# Test 4: "Criminal Code" — legislation, A2AJ prefix
run_test("4", "Criminal Code — legislation (A2AJ path)",
         "legislation_a2aj", False,
         lambda: tab1_citation_query("Criminal Code"),
         timeout_s=60)

# Test 5: "Employment Standards Act" (Alberta) — CanLII fallback
run_test("5", "Employment Standards Act — legislation (CanLII fallback, AB)",
         "legislation_canlii", False,
         lambda: tab1_citation_query("Employment Standards Act"),
         timeout_s=120)

# Test 6: "Bill C-11" — bill, LEGISinfo
run_test("6", "Bill C-11 — bill (LEGISinfo, deterministic)",
         "bill", True,
         lambda: tab1_citation_query("Bill C-11"),
         timeout_s=60)

# Test 7: DOI journal — CrossRef deterministic
run_test("7", "DOI journal article (Tab3/DOI — CrossRef deterministic)",
         "url/doi/journal", True,
         lambda: tab3_journal_by_doi("10.2139/ssrn.2780340"),
         timeout_s=30)

# Test 8: Journal WITHOUT DOI — LLM concept degrade path
# (Entering a journal article description gets classified as "concept" in Tab1)
run_test("8", "Journal article without DOI — LLM concept degrade path (Tab1)",
         "concept_llm", False,
         lambda: tab1_citation_query("Canadian environmental law and climate change policy"),
         timeout_s=120)

# Test 9: Book with ISBN — Open Library deterministic
run_test("9", "Book via ISBN (Tab3/ISBN — Open Library deterministic)",
         "url/isbn/book", True,
         lambda: tab3_book_by_isbn("9780439858045"),
         timeout_s=30)

# ── Tab2: File / Image ────────────────────────────────────────

# Test 10: File extraction via the data/test.docx sample file
DOCX_PATH = os.path.join(_PROJ, "data", "test.docx")
if os.path.exists(DOCX_PATH):
    def test_file_extract():
        fields = extract_from_file(DOCX_PATH)
        if "error" in fields:
            return fields
        raw_text = fields.get("raw_text", "") or ""
        doc_type = classify_document_type(raw_text)
        citation = format_citation(fields, doc_type=doc_type)
        return {"doc_type": doc_type, "citation_preview": citation[:150]}

    run_test("10", "File extraction via data/test.docx (Tab2)",
             "file_extract", False,
             test_file_extract,
             timeout_s=60)
else:
    print(f"\n  [!] SKIP Test 10: no test file at {DOCX_PATH}")
    results.append({"test_id": "10", "description": "File extraction (no fixture)",
                    "route": "file_extract", "deterministic": False,
                    "total_ms": 0, "status": "SKIPPED", "detail": "No test file available",
                    "profile": {"records": [], "slowest": None, "retry_labels": set()}})

# ── Tab3: URL Extraction ──────────────────────────────────────

# Test 11: Supported URL (CanLII case page)
run_test("11", "CanLII case URL (Tab3 — URL extraction + classify + format)",
         "url/canlii", False,
         lambda: tab3_url_extract("https://www.canlii.org/en/ca/scc/doc/2022/2022scc39/2022scc39.html"),
         timeout_s=60)

# Test 12: JS-rendered URL (ourcommons.ca — expected unsupported)
run_test("12", "ourcommons.ca JS-rendered URL (Tab3 — expected unsupported)",
         "url/js_rendered", False,
         lambda: tab3_url_unsupported("https://www.ourcommons.ca"),
         timeout_s=30)


# ══════════════════════════════════════════════════════════════════
#  SUMMARY TABLE
# ══════════════════════════════════════════════════════════════════

print("\n\n")
print("=" * 120)
print("  LATENCY PROFILING — SUMMARY TABLE")
print("=" * 120)

header = f"{'Test':<6} {'Route':<25} {'Total (ms)':<12} {'Slowest Step':<50} {'Retry?':<8} {'Assembly':<14} Notes"
sep = "-" * 120
print(header)
print(sep)

for r in results:
    tid = r["test_id"]
    route = r["route"][:24]
    total = f"{r['total_ms']:.0f}" if r["total_ms"] else "SKIP"

    prof_data = r.get("profile", {})
    slowest = prof_data.get("slowest")
    retries = prof_data.get("retry_labels", set())

    if slowest:
        slowest_name = slowest["label"][:35]
        slowest_ms = slowest["duration_s"] * 1000
        slowest_str = f"{slowest_name} ({slowest_ms:.0f}ms)"
    else:
        slowest_str = "(no profile data)" if r["status"] != "SKIPPED" else "—"

    retry_str = "Y" if retries else "N"
    assembly = "deterministic" if r["deterministic"] else "LLM"
    if r["status"] == "SKIPPED":
        assembly = "—"

    # Notes
    notes = ""
    if r["status"] == "ERROR":
        notes = f"ERR {r['detail'][:60]}".replace("\n", " ")
    elif r["status"] == "SKIPPED":
        notes = "No test fixture available"
    elif tid == "1" and isinstance(r.get("total_ms"), (int, float)) and r["total_ms"] > 60000:
        notes = "SUSPECT -- over 60s"
    elif tid == "5" and isinstance(r.get("total_ms"), (int, float)) and r["total_ms"] > 10000:
        notes = "CanLII browse may be slow"

    print(f"{tid:<6} {route:<25} {total:<12} {slowest_str:<50} {retry_str:<8} {assembly:<14} {notes}")

print(sep)

# Timing diagnostics summary
print("\n  TIMING DIAGNOSTICS:")
high_total = [r for r in results if r["total_ms"] > 2000 and r["status"] not in ("SKIPPED",)]
if high_total:
    print(f"  [!] {len(high_total)} test(s) with total > 2s:")
    for r in high_total:
        print(f"     Test {r['test_id']}: {r['description'][:70]} — {r['total_ms']:.0f}ms total")
else:
    print("  ✓  All tests completed within 2s threshold")

# Check for retries
all_retries = set()
for r in results:
    for lbl in r.get("profile", {}).get("retry_labels", set()):
        all_retries.add(lbl)
if all_retries:
    print(f"  [!] Retried operations detected: {all_retries}")
else:
    print("  ✓  No retry patterns detected")

# Check for serial-external-calls opportunities
print("\n  SERIAL EXTERNAL CALL ANALYSIS:")
for r in results:
    recs = r.get("profile", {}).get("records", [])
    http_calls = [rec for rec in recs if rec["label"].startswith("http.")]
    if len(http_calls) > 1:
        http_total = sum(rec["duration_s"] for rec in http_calls)
        http_in_order = sum(rec["duration_s"] for rec in sorted(http_calls, key=lambda x: x["start_s"]))
        print(f"     Test {r['test_id']}: {len(http_calls)} serial ext calls = {http_total*1000:.0f}ms total")

print("\n" + "=" * 120)
print(f"  Profiling completed: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 120)
