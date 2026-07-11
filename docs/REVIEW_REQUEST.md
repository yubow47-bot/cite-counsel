# Review Request: Verified Gate for Concept / citation_select

**Date:** 2026-07-11
**Author:** Claude Code

---

## Summary

Three gaps allowed unverified concept candidates to reach `format_citation()`, producing fabricated citations. Each was fixed by gating on `item.get("verified")`.

### Change A — `_handle_concept()` single-result branch (gap #1)

**File:** `api/main.py` — `_handle_concept()` function

**Before:** The single-result branch (`len(results) == 1`) called `format_citation()` unconditionally, with no `verified` check — unlike the `citation_number`/`legislation`/`bill` routes earlier in `citation_query()`.

**After:**
- Signature changed from `_handle_concept(results: list) -> dict` to `_handle_concept(results: list, query: str) -> dict` so `build_prefill("concept", query, partial=item)` can be called.
- Single unverified result → `_scaffold_response("concept", …, prefill=build_prefill("concept", query, partial=item), …)` — same pattern as the other routes.
- Single verified result → `_format_concept(item)` (extracted helper, unchanged behavior).
- New helpers `_format_concept()` and `_concept_scaffold()` extracted to avoid duplication.

### Change B — `_handle_concept()` multi-candidate branch (gap #2)

**File:** `api/main.py` — `_handle_concept()` function

**Before:** All results (verified or not) were included in the `needs_selection` candidate list.

**After:** Results are filtered to `verified`-truthy items before building the candidate list:
- **Empty after filtering** → same `_concept_scaffold()` response as the existing empty-results branch.
- **Exactly 1 after filtering** → `_format_concept(item)` (formats directly, status `"done"`).
- **2+ after filtering** → `needs_selection` with only verified candidates.

### Change C — `citation_select()` endpoint (gap #3)

**File:** `api/main.py` — `citation_select()` function

**Before:** No `verified` check — any candidate the user picked would be formatted.

**After:** Before calling `format_citation()`, checks `item.get("verified") is not True` (explicit boolean identity — rejects `"false"`, `1`, or any truthy-but-non-True value the same as an actual `False`). If not `True`, returns `status: "unsupported"` with `_SCAFFOLD_DISABLED_MSG`. The frontend already handles `"unsupported"` status (see `CitationTool.applyEnvelope()` and `CitationStatus` type which includes `"unsupported"`).

**Architectural note:** This check trusts the `verified` field as sent back by the client in the request body, same as the rest of `citation_select()`'s stateless design (it already trusts every other field — `statute_title`, `neutral_citation`, etc. — for formatting). This is a known, pre-existing architectural property of this endpoint, not something this task changes.

### Not touched

- `expand_concept()` in `local_tools/citation_search.py` — no changes.
- `_verify_legislation()` — no changes.
- Any route logic in `citation_query()` outside the `_handle_concept()` call site update.

## Call-site search results (pre-change verification)

### `_handle_concept(` call sites

**Finding:** Exactly 1 call site — `citation_query()` at `api/main.py:376`. The only other occurrence is the function definition at line 442. ✅ Safe to modify the signature.

### `citation_select` call sites

**Backend:** 1 endpoint definition (`@app.post("/api/citation/select")` at line 494).

**Frontend:** `citation-api.ts:71` calls it via `postCitationSelect()` returning `Envelope`. The `CitationStatus` type (line 6-11) already includes `"unsupported"` (`"done" | "needs_selection" | "needs_input" | "unsupported" | "error"`). The `CitationTool` component's `applyEnvelope()` (line 42-82) already has a `case "unsupported"` handler at lines 64-70. ✅ The frontend will correctly display the "unsupported" state when `citation_select` returns it.

**Only existing test that needed updating:** `test_citation_select_has_pinpoint_field` in `test_api_envelope.py` — its candidate dict lacked `verified: True`, so the new gate correctly rejected it. Updated to include `"verified": True`.

## Test Results

```
439 passed, 2 skipped, 17 warnings
```

All 8 regression tests pass (in `tests/test_verified_gate.py`):

| # | Test | Status | What it verifies |
|---|------|--------|------------------|
| 1 | `test_1_unverified_single_returns_scaffold` | ✅ | Single unverified → scaffold, `format_citation` never called |
| 2 | `test_2_verified_single_returns_done` | ✅ | Single verified → status `"done"` (unchanged) |
| 3 | `test_3_mixed_three_filters_candidates` | ✅ | 3 mixed → only 2 verified in `needs_selection` |
| 4 | `test_4_all_unverified_returns_scaffold` | ✅ | All unverified → same scaffold as empty results |
| 5 | `test_5_unverified_verified_pair_formats_directly` | ✅ | 1 verified after filtering → `"done"` |
| 6 | `test_6_select_unverified_returns_unsupported` | ✅ | `citation_select` with `verified=False` → `"unsupported"`, `format_citation` never called |
| 7 | `test_7_select_verified_returns_done` | ✅ | `citation_select` with `verified=True` → `"done"` (unchanged) |
| 8 | `test_8_select_truthy_non_true_returns_unsupported` | ✅ | `citation_select` with `verified="false"`, `1`, `"yes"`, `"True"` → all `"unsupported"` — proves `is not True` identity check, not truthiness |

No existing tests regressed.
