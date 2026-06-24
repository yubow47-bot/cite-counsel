# Follow-ups

## Pre-existing test failures (confirmed unrelated to DeepSeek spend tracking)

Verified via archive-tree baseline diff (HEAD~1 vs HEAD) per the verification protocol.
All 7 failures exist on the unchanged `32aeddb` baseline, and each traceback is
identical between HEAD~1 (`32aeddb`) and HEAD (`5bb03ec`). None of the tracebacks
contain `ImportError`, `ModuleNotFoundError`, `circular`, `spend_tracker`, or
`record_cost`.

### `test_api_envelope.py` (2 failures)

- **`test_assemble_response_contains_source_type`** — asserts `body["status"] == "done"`
  but gets `"unsupported"` because `SCAFFOLD_ENABLED` is `False` by default.
  Patch the env var or the config gate to enable scaffold in tests.
- **`test_assemble_response_verified_false_and_source_type`** — same root cause:
  scaffold disabled, so `citations` list is empty instead of containing one entry.

### `test_file_extractor.py` (4 failures)

- **`test_extract_from_file_png`**, **`test_extract_from_file_jpg`**,
  **`test_extract_from_file_jpeg`**, **`test_extract_from_file_webp`** —
  all raise `KeyError: 'page_title'`. The mocked `extract_from_image` returns
  `{"page_title": "Test"}` but `extract_from_file` does not pass through the
  result from `extract_from_image` to its own return value. The function either
  returns a different dict or the mock patch target is wrong.

### `test_url_extract.py` (1 failure)

- **`TestExtractUrlOnly.test_url_only`** — asserts `body["status"] == "done"`
  but gets `"unsupported"`. `extract_from_url` and `format_citation` are both
  mocked, so the returned status depends on a path condition that the mocks
  trigger differently than expected (possibly `raw_text` length check or
  `classify_document_type` call).
