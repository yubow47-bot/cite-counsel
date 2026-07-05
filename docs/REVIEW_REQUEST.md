# Review Request: Concept-Route Legislation Pinpoint Duplication

**Date:** 2026-07-05  
**Author:** Claude Code  
**Reviewer:** Yubo (sole committer)

---

## Summary

Two independent code paths produced legislation-route citation candidates with
inconsistent field schemas, and one API endpoint applied a case-only UI
behavior indiscriminately to all routes. Together this caused a duplicated
pinpoint in concept-route legislation citations plus a spurious editable
pinpoint box in the frontend.

**HEAD before fix:** `ead5275` — `@ Fix: _verify_case() name-search fallback no longer blindly trusts A2AJ index 0`  
**HEAD after fix:** `4755146` — this commit

---

## Diff

### `local_tools/citation_search.py` — Bug 1 fix

**`_verify_legislation()`**: Stopped returning ad-hoc `name` and `neutral_citation`
fields.  Now returns the same clean schema as the direct legislation branch of
`search_citation()`: `statute_title`, `jurisdiction`, `chapter`, `pinpoint`,
`citation`, `verified` (plus `warning` on failure).

- `chapter` is extracted from the citation string via `re.search(r'(c\s[\w.-]+)', ...)`
- `jurisdiction` is extracted from the A2AJ dataset field via `_extract_jurisdiction()`
- `citation` is set from `citation_en` (or `base_citation` as fallback)

**`verify_one()`**: After merging `_verify_legislation` result, cleans up the
ad-hoc `name` and `neutral_citation` fields that were set from the LLM
candidate shape.  The `role: "legislation"` key is preserved for `detect_type()`.

Added a broader pinpoint fallback regex for constitutional titles whose
citation number does not match `_CITATION_REGEX` (e.g. "Canadian Charter of
Rights and Freedoms, s 1").

```diff
-        if _pin and cit_match:
-            entry["name"] = normalized[:cit_match.end()].strip().rstrip(',').strip()
+
+        chapter = None
+        if cit_match:
+            ch_match = re.search(r'(c\s[\w.-]+)', base_citation)
+            if ch_match:
+                chapter = ch_match.group(1)
+        if chapter:
+            entry["chapter"] = chapter
```

```diff
-                entry["neutral_citation"] = results[0].get("citation_en", base_citation)
+                cit_en = results[0].get("citation_en", base_citation)
+                entry["citation"] = cit_en
+                if not chapter:
+                    ch_match = re.search(r'(c\s[\w.-]+)', cit_en)
+                    if ch_match:
+                        entry["chapter"] = ch_match.group(1)
+                jur = _extract_jurisdiction(results[0].get("dataset", ""))
+                if jur:
+                    entry["jurisdiction"] = jur
```

```diff
         if ctype == "legislation":
             result = _verify_legislation(name)
             entry.update(result)
+            if not entry.get("pinpoint") and name:
+                _broader = re.search(
+                    r'(?:,\s*)?((?:s|ss|art|cl|para|sub)\.?\s*[\d(][\d\w().,-]*(?:\s*\([\w\d]+\))*)\s*$',
+                    name, re.IGNORECASE
+                )
+                if _broader:
+                    entry["pinpoint"] = _broader.group(1)
+            entry.pop("name", None)
+            entry.pop("neutral_citation", None)
             return entry
```

### `api/main.py` — Bug 2 fix

**`citation_select()`**: Only strip `pinpoint` into a separate response field
when the candidate represents a case/jurisprudence result.  Case detection
uses `item.get("role") == "case"` for concept-route items, and
`style_of_cause` without `bill_session` for direct case_name items.

```diff
-        _pin = item.get("pinpoint")
-        if _pin:
+        _is_case = (
+            item.get("role") == "case"
+            or (bool(item.get("style_of_cause")) and not item.get("bill_session"))
+        )
+        _pin = item.get("pinpoint")
+        if _pin and _is_case:
             fmt_item = {k: v for k, v in item.items() if k != "pinpoint"}
             citation = format_citation(_without_internal(fmt_item))
         else:
             citation = format_citation(_without_internal(item))
         ...
-        if _pin:
+        if _pin and _is_case:
             _cit_data["pinpoint"] = _pin
```

---

## Test Output

### Full suite (without test_crossref.py, the standing exclusion)

```
$ python -m pytest tests/ --ignore=tests/test_crossref.py -q
418 passed, 2 skipped, 16 warnings in 33.91s
```

### Pinpoint-specific tests (verbose)

```
$ python -m pytest tests/test_pinpoint_regression.py tests/test_api_envelope.py -v

tests/test_pinpoint_regression.py::test_direct_legislation_pinpoint_once PASSED
tests/test_pinpoint_regression.py::test_concept_legislation_via_select_pinpoint_once PASSED
tests/test_pinpoint_regression.py::test_case_citation_select_has_pinpoint_field PASSED
tests/test_pinpoint_regression.py::test_verify_legislation_clean_schema PASSED
tests/test_api_envelope.py::test_legislation_route_no_pinpoint_field PASSED
tests/test_api_envelope.py::test_case_route_has_pinpoint_field PASSED
tests/test_api_envelope.py::test_concept_route_no_pinpoint_field PASSED
tests/test_api_envelope.py::test_citation_select_has_pinpoint_field PASSED
(All 12 passed)
```

---

## Regression Count

| Metric | HEAD (`ead5275`) | After fix (`4755146`) |
|---|---|---|
| Tests passed | 416 | 418 |
| Tests skipped | 2 | 2 |
| Tests failed | 2 | 0 |
| Total collected | 420 | 420 |

The 2 tests that failed at HEAD (`test_concept_legislation_via_select_pinpoint_once`,
`test_verify_legislation_clean_schema`) are new regression tests that explicitly
assert the fixed behavior — they could not pass without the code changes.

---

## Files Changed

```
 api/main.py                      | 21 ++++++++++++++---
 local_tools/citation_search.py   | 51 +++++++++++++++++++++++++++++++---
 tests/test_api_envelope.py       | 16 +++++++------
 tests/test_canlii_fallback.py    | 39 +++++++++++++++++----------
 tests/test_classify_normalize.py | 13 +++++-----
 tests/test_pinpoint_regression.py | 69 +++++++++++++++++++++++++++++++++++++++
 6 files changed, 329 insertions(+), 34 deletions(-)
```

---

## Verification Command

```bash
python -m pytest tests/ --ignore=tests/test_crossref.py -q
```
