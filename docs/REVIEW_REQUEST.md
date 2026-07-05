# Review Request: Fix `_verify_case()` Name-Search Fallback

**Date:** 2026-07-05
**Author:** Claude Code (based on bug report by Yubo)
**Reviewer:** Yubo (sole committer)

---

## Problem

Critical correctness bug in `expand_concept()`'s `_verify_case()` (nested function inside `citation_search.py`). When the LLM-provided citation fails to resolve via `fetch_by_citation()`, the fallback path does:

```python
results = search_cases_multi(name, size=1, search_type="name")
if results:
    mapped = _map_fields(results[0])
    entry["verified"] = True
```

This blindly trusts A2AJ's first name-search result as `verified=True` with **zero cross-check**.

**Confirmed production incident:** For "mens rea", Gemini generated "R v Creighton" with citation "1993 3 SCR 484" (wrong page number — real citation is [1993] 3 SCR 3). `fetch_by_citation()` failed, triggering name-search fallback for "R v Creighton". A2AJ returned a completely different case — "R v Creighton, 2022 BCSC 1728" — because "Creighton" is a common surname with multiple unrelated same-named cases. This wrong case was marked `verified=True` and would be displayed to the user with no indication it's the wrong case.

---

## Fix

### `local_tools/citation_search.py` — `_verify_case()` fallback rewrite

The nested function `_verify_case()` inside `expand_concept()` had its name-search fallback path rewritten:

**Before (buggy):** 7 lines, `size=1`, blind index-0 trust.

**After (fixed):** ~70 lines with structured verification:

1. **Primary path (unchanged):** `fetch_by_citation()` — if citation resolves, return immediately
2. **Year extraction:** Reuse `_extract_year()` (already imported from `a2aj_api.py`) on the LLM-provided citation string
3. **Year-bounded path (year available):**
   - `search_cases_multi(name, size=5, search_type="name", start_date="YYYY-01-01", end_date="YYYY-12-31")`
   - Apply name-normalization matching (strip `R v / R c / Regina v / The Queen v` prefix with optional dots, remove periods, lowercase)
   - Exactly 1 match → `verified=True`
   - 0 matches → `verified=False` ("⚠️ 未能在数据库验证该判例") — no wider fallback
   - 2+ matches → `verified=False` with year-specific ambiguous warning
4. **Unbounded path (no year extractable):**
   - `search_cases_multi(name, size=5, search_type="name")` with same normalization
   - Exactly 1 match → `verified=True`
   - 0 or 2+ → `verified=False` with general ambiguous warning

### Key design decisions:
- **False-negative-over-false-positive** — zero results after year-bounding does NOT fall back to unbounded search
- **Name normalization handles dots** — A2AJ returns "R. v. Creighton" with dots; the regex now handles `R\.?\s+v\.?` so both "R v Creighton" and "R. v. Creighton" normalize to "creighton"
- **Both Gemini and DeepSeek paths** call the same `_verify_case()` — no duplication needed

---

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `local_tools/citation_search.py` | Rewrote `_verify_case()` fallback name-search path | +76 / -7 |
| `tests/test_verify_case_fix.py` | New regression tests for the fix | +324 |

No other files were modified.

---

## Git Diff

```diff
diff --git a/local_tools/citation_search.py b/local_tools/citation_search.py
index 6b702a7..3fcf419 100644
--- a/local_tools/citation_search.py
+++ b/local_tools/citation_search.py
@@ -288,6 +288,8 @@ Rules:
     def _verify_case(name: str, citation: str) -> dict:
         """验证判例候选：citation 优先 /fetch，失败/为空则按案名搜索。"""
         entry = {"verified": False}
+
+        # ── Primary path: verify via fetch_by_citation ──
         if citation:
             try:
                 verified = fetch_by_citation(citation)
@@ -297,16 +299,83 @@ Rules:
                     return entry
             except Exception:
                 pass
-        if name:
-            try:
-                results = search_cases_multi(name, size=1, search_type="name")
-                if results:
-                    mapped = _map_fields(results[0])
+
+        # ── Fallback: name search with date-bounding & name-normalization matching ──
+        if not name:
+            entry["warning"] = "⚠️ 未能在数据库验证该判例"
+            return entry
+
+        # Build name-normalization: strip R v / R c / Regina v / The Queen v
+        # prefix (with optional dots, as A2AJ returns "R. v. Creighton"),
+        # then strip remaining punctuation and lowercase for comparison.
+        _prefix_re = re.compile(
+            r"^(?:R\.?\s+v\.?|R\.?\s+c\.?|Regina\s+v\.?|The\s+Queen\s+v\.?)\s+",
+            re.IGNORECASE,
+        )
+
+        def _normalize_case_name(s: str) -> str:
+            """Normalize case name: strip prefix, remove dots, lowercase."""
+            s = _prefix_re.sub("", s)
+            s = s.replace(".", "")
+            return s.strip().lower()
+
+        name_key = _normalize_case_name(name)
+
+        # Try to extract year from the LLM-provided citation for date-bounding
+        _, year = _extract_year(citation) if citation else (None, None)
+
+        def _filter_by_name(results: list) -> list:
+            """Filter results by name normalization; return matched list."""
+            matched = []
+            for r in results:
+                result_name = r.get("name_en", "")
+                if _normalize_case_name(result_name) == name_key:
+                    matched.append(r)
+            return matched
+
+        try:
+            if year:
+                results = search_cases_multi(
+                    name, size=5, search_type="name",
+                    start_date=f"{year}-01-01", end_date=f"{year}-12-31",
+                )
+                if not results:
+                    entry["warning"] = "⚠️ 未能在数据库验证该判例"
+                    return entry
+                matched = _filter_by_name(results)
+                if len(matched) == 1:
+                    mapped = _map_fields(matched[0])
+                    entry.update(mapped)
                     entry["verified"] = True
+                    return entry
+                elif len(matched) == 0:
+                    entry["warning"] = "⚠️ 未能在数据库验证该判例"
+                    return entry
+                else:
+                    entry["warning"] = "⚠️ 该案名在同一年份存在多个同名判例，无法确认具体是哪一个"
+                    return entry
+            else:
+                results = search_cases_multi(name, size=5, search_type="name")
+                if not results:
+                    entry["warning"] = "⚠️ 未能在数据库验证该判例"
+                    return entry
+                matched = _filter_by_name(results)
+                if len(matched) == 1:
+                    mapped = _map_fields(matched[0])
                     entry.update(mapped)
+                    entry["verified"] = True
                     return entry
-            except Exception:
-                pass
+                else:
+                    entry["warning"] = "⚠️ 该案名存在多个同名判例，无法确认具体是哪一个"
+                    return entry
+        except Exception:
+            pass
+
         entry["warning"] = "⚠️ 未能在数据库验证该判例"
         return entry
```

---

## Acceptance Criteria Results

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Creighton incident: wrong citation "1993 3 SCR 484" → year-bounded search resolves to [1993] 3 SCR 3 | ✅ PASS | `test_creighton_incident_year_bounded_resolves_correctly` — verifies year bounds are passed to A2AJ and result is the real SCC case |
| 2 | Same name, no citation → never blindly pick index 0 | ✅ PASS | `test_creighton_no_citation_ambiguous_returns_unverified` — both SCC and BCSC returned, verified=False with ambiguous warning |
| 3 | Multi-match even after year-bounding → verified=False with year-ambiguous warning | ✅ PASS | `test_multi_match_same_year_returns_unverified` — two "R. v. Smith" in same year, returns verified=False |
| 4 | Correct citation (primary path) → no regression | ✅ PASS | `test_correct_citation_primary_path_unaffected` — fetch_by_citation resolves, fallback never reached |
| 5 | Year-bounded search returns 0 results → verified=False, no wider fallback | ✅ PASS | `test_year_bounded_zero_results_verified_false` — search_cases_multi not called again |
| 6 | French prefix "R c" normalizes correctly to match "R. v." result | ✅ PASS | `test_french_prefix_normalized_correctly` — "R c Creighton" matched to "R. v. Creighton" |
| 7 | No year + 0 name-normalization matches → verified=False | ✅ PASS | `test_no_year_zero_name_matches` — "R v Creighton" doesn't match "Creighton v R" |
| 8 | Full test suite no regression | ✅ PASS | 414 passed (407 baseline + 7 new), 2 skipped (live API), 0 failed |
| 9 | Both Gemini and DeepSeek paths covered | ✅ BY DESIGN | Both call `_verify_items()` → `verify_one()` → `_verify_case()` |

### Re-verification Result for "R v Creighton"

When `citation="1993 3 SCR 484"` (wrong page), `name="R v Creighton"`:

- **Code path taken:** Year-bounded (year `1993` extracted from citation via `_extract_year`)
- **fetch_by_citation attempt:** Failed (wrong page "484" doesn't exist)
- **Name search:** `search_cases_multi("R v Creighton", size=5, start_date="1993-01-01", end_date="1993-12-31")`
- **Name normalization:** "R v Creighton" → "creighton"; A2AJ "R. v. Creighton" → "creighton" ✅ match
- **Year bounding eliminates** the 2022 BCSC case (date range limited to 1993)
- **Result:** Resolves to `[1993] 3 SCR 3` with `verified=True`, or marks `verified=False` if year-bounded search returns no match
- **Never** resolves to `2022 BCSC 1728`

For `citation=None` (no citation):
- **Code path taken:** Unbounded name search
- **Name normalization:** Both "R. v. Creighton" (1993 SCC) and "R. v. Creighton" (2022 BCSC) normalize to "creighton"
- **Result:** 2+ matches → `verified=False` with warning "⚠️ 该案名存在多个同名判例，无法确认具体是哪一个"
- **Never** picks index 0 blindly

---

## Test Evidence

### Full test suite (pre-fix baseline: 407 passed)

```
$ python -m pytest tests/ --ignore=tests/test_crossref.py -q
414 passed, 2 skipped, 17 warnings in 25.57s
```

### New regression tests (7/7 pass)

```
$ python -m pytest tests/test_verify_case_fix.py -v
...
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_creighton_incident_year_bounded_resolves_correctly PASSED
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_creighton_no_citation_ambiguous_returns_unverified PASSED
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_multi_match_same_year_returns_unverified PASSED
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_correct_citation_primary_path_unaffected PASSED
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_year_bounded_zero_results_verified_false PASSED
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_french_prefix_normalized_correctly PASSED
tests/test_verify_case_fix.py::TestVerifyCaseFix::test_no_year_zero_name_matches PASSED
======================== 7 passed, 2 skipped in 0.16s =========================
```

### Test methodology

All tests use `unittest.mock.patch` to mock both `fetch_by_citation` and `search_cases_multi` (A2AJ functions), plus `call_gemini_text_structured` (LLM). This means:
- ✅ No live API calls
- ✅ Deterministic, reproducible test data
- ✅ Full coverage of the `_verify_case` → `verify_one` → `_verify_items` → `expand_concept` call chain

### Live `expand_concept("mens rea")` test

The test `test_live_expand_concept_mens_rea_round1` (annotated `@pytest.mark.skip` — requires live API) exists for manual execution. It prints all candidates and asserts that if "Creighton" appears, it is NOT verified with "2022 BCSC" citation.

To run: `python -m pytest tests/test_verify_case_fix.py -v -k "live"`

---

## Open Items

1. **Live API verification of `expand_concept("mens rea")`** — The automated tests mock A2AJ/LLM. A real end-to-end run should be performed before deploying.
2. **No change to `expand_concept`'s prompt** — The LLM may still produce wrong citations; this fix only prevents the fallback from silently resolving to wrong cases.

---

## Architecture Impact

**Minimal.** The change is scoped entirely to the nested `_verify_case()` function inside `expand_concept()`:

- No new module-level imports or dependencies
- No changes to class hierarchy or API contracts
- No changes to data structures or return types
- No changes to the primary verification path (`fetch_by_citation`)
- All existing callers (both Gemini and DeepSeek paths) go through the same `_verify_case` → zero duplication

The only behavioral change: when `fetch_by_citation` fails and we fall back to name search, we now extract a year for date-bounding, return `size=5` for disambiguation, apply name-normalization matching, and refuse to guess when results are ambiguous. This is strictly more conservative (false-negative-over-false-positive).

---

## Risk Declaration

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A valid case with a unique name produces 0 A2AJ results → verified=False | Low (A2AJ has good coverage) | Medium — user must verify manually | Deliberate design: false-negative-over-false-positive |
| Year-bounded search misses because A2AJ date differs from reported year | Very low | Low — unverified, user can search manually | A2AJ dates are authoritative; correct behavior |
| Regression in unrelated search paths | None | N/A | Only `_verify_case()` changed; all other functions untouched |
| New code throws unhandled exception | Low | Low — outer try/except catches and degrades gracefully | Entire fallback is try/except-wrapped |

---

## Regression Surface

The change only affects the name-search fallback path inside `_verify_case()`, which is only reached when:
1. `expand_concept()` is called (concept route in `search_citation()`)
2. A case candidate has a `citation` that fails `fetch_by_citation()` (or has no citation)
3. The case has a non-empty `name`

These conditions only occur during concept expansion. The citation_number, case_name, legislation, and bill routes are completely unaffected.

**Functions explicitly NOT touched:**
- `_verify_legislation()`
- `classify_and_normalize()`
- `_infer_jurisdiction_canlii()`
- `format_citation()`
- `search_citation()` case_name route
- `_bracket_reporter_year()`
- `fetch_by_citation()`

---

## Rollback Plan

1. **Revert the file:** `git checkout -- local_tools/citation_search.py tests/test_verify_case_fix.py`
2. **Or revert the commit:** `git revert <commit-hash>`
3. **Confirm rollback:** Run `python -m pytest tests/ --ignore=tests/test_crossref.py -q` — should return to 407 passed
4. **Re-deploy:** Restart the API server

The change is a single-function rewrite with no data migration or schema changes — rollback is instantaneous and risk-free.

---

## Summary

**+400 lines across 2 files** (76 added, 7 removed in the fix; 324 lines of tests).

The fix converts a blind `results[0]` trust into a structured verification pipeline:
1. Year extraction from citation → date-bounded A2AJ search
2. Name-normalization matching (prefix stripping with optional dots, case-insensitive)
3. Explicit ambiguity detection (0-match → unverified, 2+ match → warning)
4. Conservative false-negative-over-false-positive design

All 7 regression tests pass. Full test suite: 414 passed (0 regression). The exact production incident scenario is reproduced and resolved.
