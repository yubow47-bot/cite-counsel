#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic: investigate llm.format latency variance.

Read-only. No source file modifications.
Monkey-patches DeepSeek API calls in-memory to capture usage stats.
Runs each route through the full pipeline, builds the actual format prompt,
sends it to DeepSeek, and records prompt size / token counts / latency.
Also runs 5x repeated trials of slow and fast inputs.
"""

import os
import sys
import json
import time
import requests
from pathlib import Path

_PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJ))

# ────────────────────────────────────────────────────────────────
#  Monkey-patch DeepSeek _call_deepseek to capture metrics
# ────────────────────────────────────────────────────────────────
import llm_api.deepseek_api as ds_module

_debug_log = []

def _patched_call(messages, temperature=0, model=None):
    """Wraps _call_deepseek with metrics capture — no source files changed."""
    api_key = ds_module._get_api_key()
    actual_model = model or ds_module.DEEPSEEK_MODEL

    t0 = time.perf_counter()
    response = requests.post(
        ds_module.DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": actual_model,
            "messages": messages,
            "temperature": temperature,
        },
        timeout=60,
    )
    elapsed_s = time.perf_counter() - t0
    response.raise_for_status()
    data = response.json()
    usage = data.get("usage", {})

    # Track spend (mirrors what original _call_deepseek does)
    try:
        in_t = usage.get("prompt_tokens", 0)
        out_t = usage.get("completion_tokens", 0)
        if in_t or out_t:
            from core.spend_tracker import spend_tracker
            spend_tracker.record_cost("deepseek", actual_model, in_t, out_t)
    except Exception:
        pass

    content = data["choices"][0]["message"]["content"]

    entry = {
        "model": actual_model,
        "latency_s": round(elapsed_s, 3),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    # Also log the prompt itself (first message content)
    if messages:
        prompt_text = messages[0].get("content", "")
        entry["prompt_chars"] = len(prompt_text)
        entry["prompt_preview"] = prompt_text[:200] + "..." if len(prompt_text) > 200 else prompt_text
    _debug_log.append(entry)

    return content

ds_module._call_deepseek = _patched_call

# ── Import modules after patching ──────────────────────────────
from local_tools.citation_search import classify_and_normalize, search_citation
from local_tools.file_extractor import extract_from_file, classify_document_type
from core.mcgill_engine import format_citation, build_prompt, get_rules, detect_type, select_subpattern
from llm_api.deepseek_api import ask_deepseek

# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────

def build_format_prompt(fields: dict, doc_type: str | None = None) -> dict:
    """Replicate the prompt-building logic of format_citation without calling LLM.
    Returns a dict with the prompt text, detected type, subpattern, rules preview, etc."""
    clean = fields  # fields from search pipeline are already clean

    bill_cit = clean.get("_bill_citation")
    if bill_cit:
        return {"prompt": None, "deterministic": True, "builder": "bill"}

    if doc_type == "journal_article":
        return {"prompt": None, "deterministic": True, "builder": "crossref"}

    if doc_type == "book":
        return {"prompt": None, "deterministic": True, "builder": "openlibrary"}

    if doc_type is not None:
        type_map = {
            "case": "jurisprudence",
            "legislation": "legislation",
            "government_document": "government_docs",
            "journal_article": "secondary_sources.journal_articles",
            "book": "secondary_sources.books",
            "book_chapter": "secondary_sources.books",
            "thesis": "secondary_sources.theses",
            "newspaper": "secondary_sources.news_sources",
            "website": "secondary_sources.websites",
            "report": "government_docs",
            "other": "general_rules",
        }
        detected_type = type_map.get(doc_type, "general_rules")
    else:
        detected_type = detect_type(clean)

    subpattern = select_subpattern(detected_type, clean)
    rules = get_rules(detected_type, subpattern=subpattern)
    prompt = build_prompt(clean, detected_type, rules, subpattern=subpattern)

    return {
        "prompt": prompt,
        "deterministic": False,
        "detected_type": detected_type,
        "subpattern": subpattern,
        "rules_size_chars": len(json.dumps(rules, ensure_ascii=False)),
        "fields_size_chars": len(json.dumps(clean, ensure_ascii=False, indent=2)),
        "prompt_chars": len(prompt),
    }


def run_format_pipeline(fields: dict, label: str, doc_type: str | None = None) -> dict:
    """Run format_citation on fields and report metrics."""
    global _debug_log
    _debug_log = []

    # Build prompt info without calling LLM
    info = build_format_prompt(fields, doc_type=doc_type)

    t0 = time.perf_counter()
    try:
        result = format_citation(fields, doc_type=doc_type)
        elapsed_s = time.perf_counter() - t0
        ok = True
    except Exception as e:
        elapsed_s = time.perf_counter() - t0
        result = f"ERROR: {e}"
        ok = False

    api_entry = _debug_log[-1] if _debug_log else {}

    return {
        "label": label,
        "ok": ok,
        "total_latency_s": round(elapsed_s, 3),
        "prompt_chars": info.get("prompt_chars", api_entry.get("prompt_chars", 0)),
        "prompt_preview": api_entry.get("prompt_preview", "(no API call)")[:150],
        "detected_type": info.get("detected_type", ""),
        "subpattern": info.get("subpattern", ""),
        "deterministic": info.get("deterministic", False),
        "builder": info.get("builder", ""),
        "rules_size_chars": info.get("rules_size_chars", 0),
        "fields_size_chars": info.get("fields_size_chars", 0),
        "api_prompt_tokens": api_entry.get("prompt_tokens", 0),
        "api_completion_tokens": api_entry.get("completion_tokens", 0),
        "api_latency_s": api_entry.get("latency_s", 0),
        "api_model": api_entry.get("model", ""),
        "result_preview": str(result)[:150] if ok else result[:150],
    }


def run_n_times(fields: list, n: int, label: str, doc_type: str | None = None) -> list[dict]:
    """Run format_citation N times on the same input, capturing each timing."""
    trials = []
    for i in range(n):
        global _debug_log
        _debug_log = []

        t0 = time.perf_counter()
        try:
            result = format_citation(fields, doc_type=doc_type)
            elapsed_s = time.perf_counter() - t0
            ok = True
        except Exception as e:
            elapsed_s = time.perf_counter() - t0
            result = f"ERROR: {e}"
            ok = False

        api_entry = _debug_log[-1] if _debug_log else {}
        trials.append({
            "trial": i + 1,
            "label": label,
            "total_latency_s": round(elapsed_s, 3),
            "api_latency_s": api_entry.get("latency_s", 0),
            "api_prompt_tokens": api_entry.get("prompt_tokens", 0),
            "api_completion_tokens": api_entry.get("completion_tokens", 0),
            "ok": ok,
        })
    return trials


def print_header(s):
    print(f"\n{'='*80}")
    print(f"  {s}")
    print(f"{'='*80}")


# ═══════════════════════════════════════════════════════════════
#  PHASE 1: Run each route's search pipeline to get fields
# ═══════════════════════════════════════════════════════════════

print_header("PHASE 1: Building format prompts per route")

# ── Route A: Simple case "R v Oakes" ──
print("\n>>> Route A: case_name 'R v Oakes'")
classified = classify_and_normalize("R v Oakes")
results = search_citation("R v Oakes", classification=classified)
fields_a = results[0] if results else {"style_of_cause": "R v Oakes"}
print(f"   Fields: style_of_cause={fields_a.get('style_of_cause','')[:60]}, "
      f"neutral_citation={fields_a.get('neutral_citation','')}, "
      f"reporter={fields_a.get('reporter','')[:30]}")

# ── Route B: Citation number "2022 SCC 39" ──
print("\n>>> Route B: citation_number '2022 SCC 39'")
classified = classify_and_normalize("2022 SCC 39")
results = search_citation("2022 SCC 39", classification=classified)
fields_b = results[0] if results else {"neutral_citation": "2022 SCC 39"}
print(f"   Fields: style_of_cause={fields_b.get('style_of_cause','')[:60]}, "
      f"neutral_citation={fields_b.get('neutral_citation','')}, "
      f"reporter={fields_b.get('reporter','')[:30]}")

# ── Route C: Legislation A2AJ "Criminal Code" ──
print("\n>>> Route C: legislation A2AJ 'Criminal Code'")
classified = classify_and_normalize("Criminal Code")
results = search_citation("Criminal Code", classification=classified)
fields_c = results[0] if results else {"statute_title": "Criminal Code"}
print(f"   Fields: statute_title={fields_c.get('statute_title','')[:60]}, "
      f"jurisdiction={fields_c.get('jurisdiction','')}, "
      f"chapter={fields_c.get('chapter','')}")

# ── Route D: Legislation CanLII "Employment Standards Act" ──
print("\n>>> Route D: legislation CanLII 'Employment Standards Act'")
classified = classify_and_normalize("Employment Standards Act")
results = search_citation("Employment Standards Act", classification=classified)
fields_d = results[0] if results else {"statute_title": "Employment Standards Act"}
print(f"   Fields: statute_title={fields_d.get('statute_title','')[:60]}, "
      f"jurisdiction={fields_d.get('jurisdiction','')}, "
      f"chapter={fields_d.get('chapter','')}, "
      f"verified={fields_d.get('verified')}, "
      f"match_path={fields_d.get('_match_path','')}")

# ── Route E: Concept "Canadian environmental law..." ──
print("\n>>> Route E: concept 'Canadian environmental law and climate change policy'")
classified = classify_and_normalize("Canadian environmental law and climate change policy")
results = search_citation("Canadian environmental law and climate change policy", classification=classified)
# Pick first verified result (concept returns multiple)
fields_e = None
for r in (results or []):
    if r.get("verified"):
        fields_e = r
        break
fields_e = fields_e or (results[0] if results else {"style_of_cause": "Canadian Environmental Protection Act"})
print(f"   Fields: title={fields_e.get('statute_title') or fields_e.get('style_of_cause','')[:60]}, "
      f"type={fields_e.get('role','case')}, verified={fields_e.get('verified')}")

# ── Route F: Tab2 file extraction (data/test.docx) ──
print("\n>>> Route F: Tab2 file extraction 'data/test.docx'")
DOCX_PATH = os.path.join(_PROJ, "data", "test.docx")
if os.path.exists(DOCX_PATH):
    fields_f = extract_from_file(DOCX_PATH)
    doc_type_f = classify_document_type(fields_f.get("raw_text", ""))
    print(f"   Fields: title={fields_f.get('title','')[:50]}, "
          f"author={fields_f.get('author','')[:30]}, "
          f"doc_type={doc_type_f}, raw_text_len={len(fields_f.get('raw_text',''))}")
else:
    fields_f = None
    doc_type_f = None
    print("   SKIP (no test file)")


# ═══════════════════════════════════════════════════════════════
#  PHASE 2: Prompt analysis — size breakdown per route
# ═══════════════════════════════════════════════════════════════

print_header("PHASE 2: Prompt size breakdown per route")

routes = [
    ("A: case_name 'R v Oakes'", fields_a, None),
    ("B: citation_number '2022 SCC 39'", fields_b, None),
    ("C: legislation A2AJ 'Criminal Code'", fields_c, None),
    ("D: legislation CanLII 'Employment Standards Act'", fields_d, None),
    ("E: concept 'Canadian environmental law...'", fields_e, None),
]

if fields_f:
    routes.append(("F: Tab2 file test.docx", fields_f, doc_type_f))

for label, fields, doc_type in routes:
    info = build_format_prompt(fields, doc_type=doc_type)
    if info.get("deterministic"):
        print(f"\n{label}")
        print(f"   DETERMINISTIC path ({info['builder']}) — no LLM prompt built")
        continue

    prompt = info["prompt"]
    print(f"\n{label}")
    print(f"   Detected type: {info['detected_type']}")
    print(f"   Subpattern:    {info['subpattern']}")
    print(f"   Prompt chars:  {info['prompt_chars']}")
    print(f"   ~Prompt tokens: ~{info['prompt_chars'] // 4}")
    print(f"   Rules JSON:    {info['rules_size_chars']} chars")
    print(f"   Fields JSON:   {info['fields_size_chars']} chars")

    # Show breakdown
    prompt_str = str(prompt)
    preamble_end = prompt_str.find("McGill Rules for this source type:")
    rules_end = prompt_str.find("Information to format:")
    fields_end = prompt_str.find("STRICT OUTPUT RULES")

    print(f"   Breakdown:")
    print(f"     Preamble:       ~{preamble_end} chars (intro lines)")
    print(f"     Rules section:  ~{rules_end - preamble_end} chars (incl. template + examples)")
    print(f"     Fields section: ~{fields_end - rules_end} chars (extracted data)")
    print(f"     Output rules:   ~{len(prompt_str) - fields_end} chars (format instructions + italic rules)")

    # Print full prompt for the first few routes if small enough
    if info['prompt_chars'] < 3000:
        print(f"   FULL PROMPT:")
        for line in prompt_str.split('\n'):
            print(f"     {line}")
    else:
        print(f"   PROMPT PREVIEW (first 500 chars):")
        print(f"     {prompt_str[:500]}...")
        print(f"   PROMPT END (last 300 chars):")
        print(f"     ...{prompt_str[-300:]}")


# ═══════════════════════════════════════════════════════════════
#  PHASE 3: Run format_citation once per route (with DeepSeek)
# ═══════════════════════════════════════════════════════════════

print_header("PHASE 3: Format latency — single run per route with token counts")

results_log = []
for label, fields, doc_type in routes:
    r = run_format_pipeline(fields, label, doc_type=doc_type)
    results_log.append(r)
    print(f"\n{r['label']}")
    if r['deterministic']:
        print(f"   DETERMINISTIC — no LLM call")
        print(f"   Total: {r['total_latency_s']*1000:.0f}ms")
        print(f"   Result: {r['result_preview']}")
    else:
        print(f"   Detected type:  {r['detected_type']}")
        print(f"   Subpattern:     {r['subpattern']}")
        print(f"   Prompt chars:   {r['prompt_chars']}")
        print(f"   Prompt tokens:  {r['api_prompt_tokens']}")
        print(f"   Completion tok: {r['api_completion_tokens']}")
        print(f"   Total tokens:   {r['api_prompt_tokens'] + r['api_completion_tokens']}")
        print(f"   API latency:    {r['api_latency_s']*1000:.0f}ms")
        print(f"   Total latency:  {r['total_latency_s']*1000:.0f}ms")
        print(f"   Model:          {r['api_model']}")
        print(f"   Result:         {r['result_preview']}")


# ═══════════════════════════════════════════════════════════════
#  PHASE 4: Repeated trials — same input 5x
# ═══════════════════════════════════════════════════════════════

print_header("PHASE 4: Repeated trials — 5x each")

# Slow suspect: CanLII legislation (route D)
print("\n--- 5 trials: CanLII legislation 'Employment Standards Act' (format only) ---")
trials_d = run_n_times(fields_d, 5, "CanLII legislation")
latencies_d = [t["api_latency_s"] * 1000 for t in trials_d]
prompt_toks_d = [t["api_prompt_tokens"] for t in trials_d]
comp_toks_d = [t["api_completion_tokens"] for t in trials_d]
for t in trials_d:
    print(f"   Trial {t['trial']}: {t['api_latency_s']*1000:.0f}ms "
          f"(prompt={t['api_prompt_tokens']}tok, completion={t['api_completion_tokens']}tok, ok={t['ok']})")
print(f"   -> Min: {min(latencies_d):.0f}ms  Max: {max(latencies_d):.0f}ms  "
      f"Mean: {sum(latencies_d)/len(latencies_d):.0f}ms  StdDev: {(sum((x - sum(latencies_d)/len(latencies_d))**2 for x in latencies_d)/len(latencies_d))**0.5:.0f}ms")

# Fast: simple case (route A)
print("\n--- 5 trials: Case name 'R v Oakes' (format only) ---")
trials_a = run_n_times(fields_a, 5, "Simple case")
latencies_a = [t["api_latency_s"] * 1000 for t in trials_a]
for t in trials_a:
    print(f"   Trial {t['trial']}: {t['api_latency_s']*1000:.0f}ms "
          f"(prompt={t['api_prompt_tokens']}tok, completion={t['api_completion_tokens']}tok, ok={t['ok']})")
print(f"   -> Min: {min(latencies_a):.0f}ms  Max: {max(latencies_a):.0f}ms  "
      f"Mean: {sum(latencies_a)/len(latencies_a):.0f}ms  StdDev: {(sum((x - sum(latencies_a)/len(latencies_a))**2 for x in latencies_a)/len(latencies_a))**0.5:.0f}ms")

# Citation number (route B)
print("\n--- 5 trials: Citation number '2022 SCC 39' (format only) ---")
trials_b = run_n_times(fields_b, 5, "Citation number")
latencies_b = [t["api_latency_s"] * 1000 for t in trials_b]
for t in trials_b:
    print(f"   Trial {t['trial']}: {t['api_latency_s']*1000:.0f}ms "
          f"(prompt={t['api_prompt_tokens']}tok, completion={t['api_completion_tokens']}tok, ok={t['ok']})")
print(f"   -> Min: {min(latencies_b):.0f}ms  Max: {max(latencies_b):.0f}ms  "
      f"Mean: {sum(latencies_b)/len(latencies_b):.0f}ms  StdDev: {(sum((x - sum(latencies_b)/len(latencies_b))**2 for x in latencies_b)/len(latencies_b))**0.5:.0f}ms")

# Concept (route E)
print("\n--- 5 trials: Concept 'Canadian environmental law' (format only) ---")
trials_e = run_n_times(fields_e, 5, "Concept")
latencies_e = [t["api_latency_s"] * 1000 for t in trials_e]
for t in trials_e:
    print(f"   Trial {t['trial']}: {t['api_latency_s']*1000:.0f}ms "
          f"(prompt={t['api_prompt_tokens']}tok, completion={t['api_completion_tokens']}tok, ok={t['ok']})")
print(f"   -> Min: {min(latencies_e):.0f}ms  Max: {max(latencies_e):.0f}ms  "
      f"Mean: {sum(latencies_e)/len(latencies_e):.0f}ms  StdDev: {(sum((x - sum(latencies_e)/len(latencies_e))**2 for x in latencies_e)/len(latencies_e))**0.5:.0f}ms")


# ═══════════════════════════════════════════════════════════════
#  PHASE 5: Cross-check — same prompt size, different inputs
# ═══════════════════════════════════════════════════════════════

print_header("PHASE 5: Prompt-size vs latency correlation table")

print(f"\n{'Route':<50} {'Prompt ch':>10} {'Prompt tok':>11} {'Comp tok':>10} {'API lat ms':>10} {'Total ms':>10}")
print("-" * 105)
for r in results_log:
    if r['deterministic']:
        print(f"{r['label']:<50} {'—':>10} {'—':>11} {'—':>10} {'—':>10} {r['total_latency_s']*1000:>8.0f}  (deterministic)")
    else:
        print(f"{r['label']:<50} {r['prompt_chars']:>10} {r['api_prompt_tokens']:>11} "
              f"{r['api_completion_tokens']:>10} {r['api_latency_s']*1000:>8.0f} {r['total_latency_s']*1000:>8.0f}")


# ═══════════════════════════════════════════════════════════════
#  PHASE 6: Conclusion
# ═══════════════════════════════════════════════════════════════

print_header("PHASE 6: REPEATABILITY — summary of 5-trial runs")

def stats(trials):
    lat = [t["api_latency_s"] * 1000 for t in trials]
    return {
        "min_ms": min(lat),
        "max_ms": max(lat),
        "mean_ms": sum(lat) / len(lat),
        "range_ratio": max(lat) / min(lat) if min(lat) > 0 else float('inf'),
    }

s_a = stats(trials_a)
s_b = stats(trials_b)
s_d = stats(trials_d)
s_e = stats(trials_e)

print(f"\n{'Input':<55} {'Min ms':>8} {'Mean ms':>8} {'Max ms':>8} {'Max/Min':>8}")
print("-" * 90)
print(f"{'A: case_name R v Oakes':<55} {s_a['min_ms']:>8.0f} {s_a['mean_ms']:>8.0f} {s_a['max_ms']:>8.0f} {s_a['range_ratio']:>7.1f}x")
print(f"{'B: citation_number 2022 SCC 39':<55} {s_b['min_ms']:>8.0f} {s_b['mean_ms']:>8.0f} {s_b['max_ms']:>8.0f} {s_b['range_ratio']:>7.1f}x")
print(f"{'D: legislation CanLII Employment Standards Act':<55} {s_d['min_ms']:>8.0f} {s_d['mean_ms']:>8.0f} {s_d['max_ms']:>8.0f} {s_d['range_ratio']:>7.1f}x")
print(f"{'E: concept Canadian environmental law':<55} {s_e['min_ms']:>8.0f} {s_e['mean_ms']:>8.0f} {s_e['max_ms']:>8.0f} {s_e['range_ratio']:>7.1f}x")

print()
print("INTERPRETATION:")
print("  - Large Max/Min ratio (>1.5x) on same input = API-intrinsic variance")
print("  - Consistent latency across trials = prompt-driven")
print("  - Ratio near 1.0x on all = slimming won't help (pure API variance)")
