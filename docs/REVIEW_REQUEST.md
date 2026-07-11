# Review Request: Verified Gate for Concept / citation_select + Constitutional-title Shortcut

**Date:** 2026-07-12
**Author:** Claude Code

---

## Summary (Changes A–C)

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

---

## Summary (Change D — Constitutional-title Shortcut)

### Background

After Changes A–C were deployed, verified-only filtering in `_handle_concept()` revealed that constitutional documents (Charter, Constitution Acts, Canada Act) always returned `verified=False` from `expand_concept()`. These documents have no standard RSC/SC/SOR citation number, so `_verify_legislation()`'s A2AJ lookup always fails. The direct legislation route in `search_citation()` already handles these via a local `_CANONICAL` dict that returns `verified=True` immediately, before touching A2AJ.

### Change D — `local_tools/citation_search.py`

**1. Module-level constant promoted:**
The local `_CANONICAL` dict (previously defined inside `search_citation()`'s legislation branch) was promoted to a module-level constant named **`_CONSTITUTIONAL_CANONICAL`** (name chosen to avoid collision — `_CANONICAL` is generic and could conflict with future uses). The constant lives alongside `_CANLII_STATUTE_DB` and other module-level constants. `search_citation()`'s legislation branch now references the module-level constant instead of defining its own local variable — no behavior change there.

**2. `verify_one()` constitutional shortcut:**
In `verify_one()`'s legislation branch, before calling `_verify_legislation(name)`:
- Normalize `name` with `_normalize_title()` (same helper already imported and used elsewhere in this file).
- Check if the normalized name starts with any key in `_CONSTITUTIONAL_CANONICAL`.
- If matched: set `entry["verified"] = True`, `entry["statute_title"]` to the canonical display title, and extract any pinpoint from `name` using the same `_broader` regex pattern that the existing fallback code path already uses after `_verify_legislation()`. Skip `_verify_legislation()` (and therefore A2AJ) entirely for this case.
- If not matched: fall through to `_verify_legislation(name)` unchanged.

### Manual trace verification

Tracing `verify_one({"name": "Canadian Charter of Rights and Freedoms, s 2", "type": "legislation"})`:

| Step | Output |
|------|--------|
| `_normalize_title("Canadian Charter of Rights and Freedoms, s 2")` | `"canadian charter of rights and freedoms s 2"` |
| Starts with `"canadian charter of rights and freedoms"`? | ✅ Yes → `_matched_canonical = "Canadian Charter of Rights and Freedoms"` |
| `entry["verified"]` | `True` |
| `entry["statute_title"]` | `"Canadian Charter of Rights and Freedoms"` |
| `_broader` regex on `"Canadian Charter of Rights and Freedoms, s 2"` | Matches `"s 2"` — `_pin_match.group(1)` = `"s 2"` |
| `entry["pinpoint"]` | `"s 2"` |
| `_verify_legislation()` reached? | ❌ No — returned early from shortcut |

Result: `{"verified": True, "role": "legislation", "statute_title": "Canadian Charter of Rights and Freedoms", "pinpoint": "s 2"}`

### Not touched

- `_verify_legislation()`'s A2AJ logic itself — no changes.
- `expand_concept()`'s prompt or candidate generation — no changes.
- `_handle_concept()` verified-filtering logic (Changes A–B) — no changes.
- Any other branch of `search_citation()` — no changes.

---

## Call-site search results (Changes A–C pre-change verification)

### `_handle_concept(` call sites

**Finding:** Exactly 1 call site — `citation_query()` at `api/main.py:376`. The only other occurrence is the function definition at line 442. ✅ Safe to modify the signature.

### `citation_select` call sites

**Backend:** 1 endpoint definition (`@app.post("/api/citation/select")` at line 494).

**Frontend:** `citation-api.ts:71` calls it via `postCitationSelect()` returning `Envelope`. The `CitationStatus` type (line 6-11) already includes `"unsupported"` (`"done" | "needs_selection" | "needs_input" | "unsupported" | "error"`). The `CitationTool` component's `applyEnvelope()` (line 42-82) already has a `case "unsupported"` handler at lines 64-70. ✅ The frontend will correctly display the "unsupported" state when `citation_select` returns it.

**Only existing test that needed updating:** `test_citation_select_has_pinpoint_field` in `test_api_envelope.py` — its candidate dict lacked `verified: True`, so the new gate correctly rejected it. Updated to include `"verified": True`.

---

## Test Results

```
439 passed, 2 skipped, 17 warnings
```

Same baseline as previous commit — no tests regressed. Change D has no new automated tests (per task instructions).

---

## Files Changed

| File | Change |
|------|--------|
| `api/main.py` | Changes A, B, C: verified gating on `_handle_concept` and `citation_select` |
| `local_tools/citation_search.py` | Change D: promoted `_CONSTITUTIONAL_CANONICAL` to module level, added constitutional shortcut in `verify_one()` |
| `tests/test_verified_gate.py` | 8 regression tests for Changes A–C |
| `tests/test_api_envelope.py` | Added `verified: True` to existing pinpoint test candidate |
| `docs/REVIEW_REQUEST.md` | This file |
