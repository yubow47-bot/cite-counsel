# Review Request: Fallback Regex Over-Capture of Ontario Chapter Letter-Numbers

**Date:** 2026-07-05  
**Author:** Claude Code  
**Reviewer:** Yubo (sole committer)

---

## Summary

The broader pinpoint fallback regex in `verify_one()` (`local_tools/citation_search.py`)
matched Ontario-style chapter letter-number designators (e.g. `"c S.15"`, `"c S.5"`,
`"c SS.1"`) as if `"S.15"`, `"S.5"`, `"SS.1"` were section pinpoints. The root
cause was that the chapter letter `S` collides with the `s`/`ss` pinpoint-marker
tokens in the regex alternation `(?:s|ss|art|cl|para|sub)`.

## Fix

Added three guards to the fallback regex:

1. **Negative lookbehind `(?<!, c )`** — rejects a match where the captured token
   is immediately preceded by `", c "` (standard chapter-clause marker).

2. **Negative lookbehind `(?<! c )`** — rejects a match where the captured token
   is immediately preceded by `" c "` (chapter marker without preceding comma).

3. **Word-boundary `\b`** — prevents the `ss` alternation from matching at the
   second character of `"SS"` (e.g. in `"c SS.1"`, the `s` alternation would
   otherwise match at the second `S`, placing the lookbehinds at the wrong
   offset where they fail to catch the chapter-signal).

A real pinpoint always follows the complete citation (chapter clause already
closed, then a comma, then the pinpoint), so none of these guards block real
pinpoint detection.

Federal-style hyphenated chapters (`"c S-15"`) were already safe because `-`
is not in `[\d(]` and never reaches the lookbehinds.

## Diff

### `local_tools/citation_search.py` (regex fix only)

```diff
                 _broader = re.search(
-                    r'(?:,\s*)?((?:s|ss|art|cl|para|sub)\.?\s*[\d(][\d\w().,-]*(?:\s*\([\w\d]+\))*)\s*$',
+                    r'(?<!, c )(?<! c )(?:,\s*)?\b((?:s|ss|art|cl|para|sub)\.?\s*[\d(][\d\w().,-]*(?:\s*\([\w\d]+\))*)\s*$',
                     name,
                     re.IGNORECASE
                 )
```

### `tests/test_pinpoint_regression.py` (6 new tests)

- `test_fallback_rejects_ontario_chapter_S15` — `"c S.15"` must NOT produce pinpoint
- `test_fallback_rejects_ontario_chapter_S5` — `"c S.5"` must NOT produce pinpoint
- `test_fallback_rejects_ontario_chapter_SS1` — `"c SS.1"` must NOT produce pinpoint
- `test_fallback_rejects_federal_hyphen_chapter` — `"c S-15"` already safe, no regression
- `test_fallback_matches_pinpoint_after_chapter_clause` — `", s 5"` after chapter clause MUST extract
- `test_fallback_matches_constitutional_s1` — `"Charter, s 1"` MUST still extract (original fix regression guard)

## Test Output

### Full suite (specified command)

```
$ python -m pytest tests/ --ignore=tests/test_crossref.py -q
424 passed, 2 skipped, 16 warnings in 29.42s
```

### Pinpoint-specific tests (all 18)

```
$ python -m pytest tests/test_pinpoint_regression.py tests/test_api_envelope.py -v

tests/test_pinpoint_regression.py::test_direct_legislation_pinpoint_once PASSED
tests/test_pinpoint_regression.py::test_concept_legislation_via_select_pinpoint_once PASSED
tests/test_pinpoint_regression.py::test_case_citation_select_has_pinpoint_field PASSED
tests/test_pinpoint_regression.py::test_verify_legislation_clean_schema PASSED
tests/test_pinpoint_regression.py::test_fallback_rejects_ontario_chapter_S15 PASSED
tests/test_pinpoint_regression.py::test_fallback_rejects_ontario_chapter_S5 PASSED
tests/test_pinpoint_regression.py::test_fallback_rejects_ontario_chapter_SS1 PASSED
tests/test_pinpoint_regression.py::test_fallback_rejects_federal_hyphen_chapter PASSED
tests/test_pinpoint_regression.py::test_fallback_matches_pinpoint_after_chapter_clause PASSED
tests/test_pinpoint_regression.py::test_fallback_matches_constitutional_s1 PASSED
tests/test_api_envelope.py::test_assemble_scaffold_disabled_returns_unsupported PASSED
tests/test_api_envelope.py::test_assemble_scaffold_disabled_no_verified_citation PASSED
tests/test_api_envelope.py::test_jur_none_shows_specific_message PASSED
tests/test_api_envelope.py::test_other_match_path_shows_generic_message PASSED
tests/test_api_envelope.py::test_legislation_route_no_pinpoint_field PASSED
tests/test_api_envelope.py::test_case_route_has_pinpoint_field PASSED
tests/test_api_envelope.py::test_concept_route_no_pinpoint_field PASSED
tests/test_api_envelope.py::test_citation_select_has_pinpoint_field PASSED
(All 18 passed)
```

## Regression Count

| Metric | Before fix (`a9807f8`) | After fix (`86f0140`) |
|---|---|---|
| Tests passed | 418 | 424 |
| Tests skipped | 2 | 2 |
| Tests failed | 0 | 0 |
| Total collected | 420 | 426 |

The delta of +6 is the new negative regression tests. All existing tests
continue to pass unchanged.

## Regex Verification (pass/fail on each required case)

Full regex: `r'(?<!, c )(?<! c )(?:,\s*)?\b((?:s|ss|art|cl|para|sub)\.?\s*[\d(][\d\w().,-]*(?:\s*\([\w\d]+\))*)\s*$'`

| Input | Expected | Result |
|---|---|---|
| `"Some Act, RSO 1990, c S.15"` | NO match (chapter letter-number) | PASS |
| `"Some Act, RSO 1990, c S.5"` | NO match (chapter letter-number) | PASS |
| `"Some Act, RSO 1990, c SS.1"` | NO match (double-S chapter letter) | PASS |
| `"Some Act, RSC 1985, c S-15"` | NO match (federal hyphen, already safe) | PASS |
| `"Some Act, RSO 1990, c S.15, s 5"` | MATCH `"s 5"` (real pinpoint after clause) | PASS |
| `"Canadian Charter of Rights and Freedoms, s 1"` | MATCH `"s 1"` (constitutional, original use) | PASS |
| `"Criminal Code, RSC 1985, c C-46, s 718.2(e)"` | MATCH `"s 718.2(e)"` | PASS |
| `"Regulation X, para 5"` | MATCH `"para 5"` | PASS |
| `"Some Act, cl 5"` | MATCH `"cl 5"` | PASS |

## Files Changed

```
 local_tools/citation_search.py      |  12 ++-
 tests/test_pinpoint_regression.py   | 114 ++++++++++++++++++++++++++++
 2 files changed, 125 insertions(+), 1 deletion(-)
```

## Verification Command

```bash
python -m pytest tests/ --ignore=tests/test_crossref.py -q
```
