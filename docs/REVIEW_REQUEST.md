# Review Request: PDF URL short-circuit for `extract_from_url()`

**Date:** 2026-07-06
**Author:** Claude Code
**Reviewer:** Yubo (sole committer)

---

## Summary

`extract_from_url()` in `llm_api/deepseek_api.py` has no PDF text/byte parser. For
`.pdf`-suffixed URLs, two outcomes are possible: (a) the binary PDF bytes cause
`trafilatura.extract()` to fail cleanly (the safe path), or (b) the server returns
an HTML interstitial/archive-notice page that `trafilatura` parses successfully,
producing **silently wrong metadata** (title, year) with no error shown — the
dangerous path.

Since the module currently has no legitimate case where a `.pdf` URL produces correct
data, the fix is an early-return guard: if the URL ends in `.pdf` (case-insensitive,
query-string stripped), return immediately before any network call with a clear error
message directing the user to fill in citation fields manually.

---

## Diff

### `llm_api/deepseek_api.py` — 8 lines added at top of `extract_from_url()`

```diff
+    # ── PDF URL short-circuit ──
+    # We have no PDF text/byte parser in this module, so a .pdf-suffixed URL
+    # can never produce correct document data.  Return immediately before any
+    # network call to avoid silently returning archive-interstitial metadata.
+    _path = url.split("?", 1)[0]
+    if _path.lower().endswith(".pdf"):
+        return {"url": url, "error": "We can't reliably read a direct PDF link. Please fill in the citation fields manually."}
+
     import trafilatura
```

No other function in this file was touched (`fetch_html()`, `extract_url()` upstream
callers, etc. are unchanged). No changes to `api/main.py`, `local_tools/file_extractor.py`,
or any frontend file.

The error dict uses the same `{"url": url, "error": "..."}` shape already used by the
existing early-return error cases in this function (e.g. the `fetch_html`-returns-None
path on line ~185), so `api/main.py` surfaces it as `status: "unsupported"` without
any changes.

### `tests/test_extract_from_url.py` — 4 new tests, 1 import added

```diff
+import json
```

```python
+def test_extract_from_url_pdf_suffix_short_circuits():
+    with patch("llm_api.deepseek_api.fetch_html") as mock_fetch:
+        result = extract_from_url("https://example.com/document.pdf")
+    assert "error" in result
+    assert "reliably read a direct PDF link" in result["error"]
+    assert result["url"] == "https://example.com/document.pdf"
+    mock_fetch.assert_not_called()
+
+def test_extract_from_url_pdf_suffix_case_insensitive():
+    with patch("llm_api.deepseek_api.fetch_html") as mock_fetch:
+        result = extract_from_url("https://example.com/report.PDF")
+    assert "error" in result
+    assert "reliably read a direct PDF link" in result["error"]
+    mock_fetch.assert_not_called()
+
+def test_extract_from_url_pdf_suffix_with_query_string():
+    with patch("llm_api.deepseek_api.fetch_html") as mock_fetch:
+        result = extract_from_url("https://example.com/file.pdf?download=true")
+    assert "error" in result
+    assert "reliably read a direct PDF link" in result["error"]
+    mock_fetch.assert_not_called()
+
+def test_extract_from_url_non_pdf_url_unaffected():
+    fake_trafilatura_json = json.dumps({
+        "title": "Normal Article",
+        "author": "Author Name",
+        "date": "2023-06-01",
+        "hostname": "example.com",
+        "raw_text": "This is a normal article with enough text to pass the empty-body guard threshold of fifty characters in the raw text field.",
+    })
+    with (
+        patch("llm_api.deepseek_api.fetch_html", return_value="<html><body>ok</body></html>") as mock_fetch,
+        patch("trafilatura.extract", return_value=fake_trafilatura_json),
+    ):
+        result = extract_from_url("https://example.com/article")
+    assert "error" not in result
+    assert result["page_title"] == "Normal Article"
+    assert result["author"] == "Author Name"
+    mock_fetch.assert_called_once()
```

---

## Test Output

### Full suite (specified command)

```
$ python -m pytest tests/ --ignore=tests/test_crossref.py -q
431 passed, 2 skipped, 16 warnings in 24.09s
```

### Targeted tests (all 20 pass, 13 existing + 4 new PDF + 3 test_url_extract)

```
$ python -m pytest tests/test_extract_from_url.py tests/test_url_extract.py -v

tests/test_extract_from_url.py::test_extract_from_url_trafilatura_extract_raises PASSED
tests/test_extract_from_url.py::test_extract_from_url_trafilatura_extract_raises_runtime_error PASSED
tests/test_extract_from_url.py::test_extract_from_url_fetch_html_fails PASSED
tests/test_extract_from_url.py::test_extract_from_url_trafilatura_returns_none PASSED
tests/test_extract_from_url.py::test_fetch_html_fallback_succeeds PASSED
tests/test_extract_from_url.py::test_fetch_html_both_fail PASSED
tests/test_extract_from_url.py::test_fetch_html_curl_succeeds_no_fallback PASSED
tests/test_extract_from_url.py::test_fetch_html_fallback_http_error_returns_none PASSED
tests/test_extract_from_url.py::test_extract_from_url_error_does_not_claim_blocked PASSED
tests/test_extract_from_url.py::test_extract_from_url_pdf_suffix_short_circuits PASSED   # NEW
tests/test_extract_from_url.py::test_extract_from_url_pdf_suffix_case_insensitive PASSED  # NEW
tests/test_extract_from_url.py::test_extract_from_url_pdf_suffix_with_query_string PASSED # NEW
tests/test_extract_from_url.py::test_extract_from_url_non_pdf_url_unaffected PASSED       # NEW
tests/test_url_extract.py::TestExtractUrlDoi::test_doi_only PASSED
tests/test_url_extract.py::TestExtractUrlDoi::test_doi_with_url_ignored PASSED
tests/test_url_extract.py::TestExtractUrlIsbn::test_isbn_only PASSED
tests/test_url_extract.py::TestExtractUrlOnly::test_url_only PASSED
tests/test_url_extract.py::TestExtractUrlOnly::test_url_extract_failure_scaffold PASSED
tests/test_url_extract.py::TestExtractUrlEmpty::test_all_empty PASSED
tests/test_url_extract.py::TestExtractUrlEmpty::test_all_none PASSED
20 passed
```

---

## Regression Count

| Metric | Baseline (`40b47e9`) | After change |
|---|---|---|
| Tests passed | 427 | 431 |
| Tests skipped | 2 | 2 |
| Tests failed | 0 | 0 |
| Total collected | 429 | 433 |

The delta of +4 is the new PDF short-circuit tests. All existing tests in
`test_extract_from_url.py` and `test_url_extract.py` pass unchanged.

---

## Files Changed

```
 llm_api/deepseek_api.py              |  8 +++
 tests/test_extract_from_url.py       | 67 ++++++++++++++++++++++++++++
 2 files changed, 75 insertions(+)
```

## Verification Command

```bash
python -m pytest tests/ --ignore=tests/test_crossref.py -q
```
