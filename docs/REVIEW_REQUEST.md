# Review Request: `extract_case_pinpoint()` boundary gap + repo hygiene

**Date:** 2026-07-06
**Author:** Claude Code
**Reviewer:** Yubo (sole committer)

---

## Summary

Two independent items:

1. **Repo hygiene** — stale docs at root (`REVIEW_REQUEST.md`, `FOLLOW_UPS.md`) deleted; 10 one-off diagnostic/profiling artifacts moved to `profiling/`.
2. **`extract_case_pinpoint()` boundary gap** (`local_tools/utils.py`) — word-boundary guard added to prevent false-positive "at N" matches inside longer tokens; new pattern added for the `"at paras N"` (plural, single number) form.

---

## Item 1 — Files Deleted / Moved

### Deleted

| File | Reason |
|---|---|
| `REVIEW_REQUEST.md` | Stale artifact from an earlier review round (pinpoint v2), superseded by `docs/REVIEW_REQUEST.md` (2026-07-05, fallback regex over-capture fix) |
| `FOLLOW_UPS.md` | Documented 7 test failures that no longer reproduce on current HEAD (confirmed: both full-suite runs show 0 failures) |

### Moved to `profiling/`

The following 10 files were moved (not deleted — kept for potential reference):

- `diagnose_format_latency.py`
- `diag_format_smoke.py`
- `diag_result.json`
- `diag_thinking_mode.py`
- `diag_thinking_mode3.py`
- `diag_thinking_smoke.py`
- `debug_prompt.txt`
- `favicon_options.png`
- `test_format_timing.py`
- `test_run.py`

No name collisions existed in `profiling/` (existing contents: `__init__.py`, `run.py`, `timing.py`).

---

## Item 2 — Regex Diff

### `local_tools/utils.py` — `extract_case_pinpoint()`

```diff
     Recognizes trailing patterns at the END of the input:
       "at para N"       e.g. "at para 2"
       "at paras N-M"    e.g. "at paras 10-15"
+      "at paras N"      e.g. "at paras 10"
       "at N"            e.g. "at 47"
       "at p N"          e.g. "at p 5"
       "at pp N-M"       e.g. "at pp 10-15"
@@
     # Order matters: longer patterns first to avoid partial matches
     patterns = [
         r'at\s+paras\s+\d+(?:-\d+)$',
+        r'at\s+paras\s+\d+$',
         r'at\s+para\s+\d+(?:-\d+)?$',
         r'at\s+pp\s+\d+(?:-\d+)$',
         r'at\s+p\s+\d+(?:-\d+)?$',
-        r'at\s+\d+$',
+        r'\bat\s+\d+$',
     ]
```

**Change 1 — `\b` word-boundary guard (line 121):**
`r'at\s+\d+$'` → `r'\bat\s+\d+$'`

Without `\b`, a case name ending in a word followed by `at` and digits (e.g. `"R v Format10"` → the regex would see `at 10` inside `"Format10"`) could false-positive match. The `\b` ensures `at` is preceded by a word boundary (whitespace or start-of-string), not another word character.

**Change 2 — `"at paras N"` pattern (new line 117):**
`r'at\s+paras\s+\d+$'`

The existing `at\s+paras\s+\d+(?:-\d+)$` requires the `-M` range suffix (the `(?:-\d+)` group is not optional). Inputs like `"at paras 10"` (single number, no dash) silently returned `""`, discarding the pinpoint. The new pattern matches the standalone `"at paras N"` form.

### `tests/local_tools/test_format_util.py` — 3 new tests

```python
def test_paras_plural_single_number(self):
    """'at paras N' (plural, single number, no range) is now matched."""
    assert extract_case_pinpoint("r v smith at paras 10") == "at paras 10"

def test_no_boundary_false_positive(self):
    """Word-boundary guard on 'at' prevents match inside a longer token."""
    assert extract_case_pinpoint("R v Format10") == ""

def test_existing_paras_range_still_works(self):
    """Regression guard: 'at paras N-M' still matches the whole range."""
    assert extract_case_pinpoint("r v jones at paras 10-15") == "at paras 10-15"
```

### `extract_pinpoint()` — untouched

`extract_pinpoint()` (separate function in the same file, lines 130–149) was deliberately left byte-for-byte unchanged. No changes were made to `citation_search.py` or any other file.

---

## Test Output

### Run 1 — Before deleting `FOLLOW_UPS.md` (to confirm 7 prior failures gone)

```
$ python -m pytest tests/ --ignore=tests/test_crossref.py -q
427 passed, 2 skipped, 16 warnings in 38.50s
```

### Run 2 — Final run after all changes

```
$ python -m pytest tests/ --ignore=tests/test_crossref.py -q
427 passed, 2 skipped, 16 warnings in 27.55s
```

### Pinpoint-specific tests (all 32 pass)

```
$ python -m pytest tests/local_tools/test_format_util.py -v
...
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_pinpoint_present PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_no_pinpoint PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_act_level_only PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_sc_regulation PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_sor_regulation PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_bc_regulation PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_empty_string PASSED
tests/local_tools/test_format_util.py::TestExtractPinpoint::test_no_citation_match PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_at_para_n PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_at_paras_range PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_bare_at_number PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_at_p_n PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_at_pp_range PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_no_pinpoint_no_match PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_trailing_period_no_match PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_citation_number_no_match PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_reporter_no_match PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_pinpoint_not_at_end_no_match PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_empty_string PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_none_input PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_at_para_single_digit PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_case_insensitive PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_paras_plural_single_number PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_no_boundary_false_positive PASSED
tests/local_tools/test_format_util.py::TestExtractCasePinpoint::test_existing_paras_range_still_works PASSED
32 passed in 0.15s
```

---

## Regression Count

| Metric | Baseline (`40b47e9`) | After changes (current HEAD) |
|---|---|---|
| Tests passed | 424 | 427 |
| Tests skipped | 2 | 2 |
| Tests failed | 0 | 0 |
| Total collected | 426 | 429 |

The delta of +3 is the new pinpoint-boundary regression tests. All 15 pre-existing `TestExtractCasePinpoint` tests and all 8 `TestExtractPinpoint` tests pass unchanged.

---

## Verification Command

```bash
python -m pytest tests/ --ignore=tests/test_crossref.py -q
```
