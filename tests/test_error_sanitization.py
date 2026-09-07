"""Regression tests: raw Python exception strings must never reach the frontend.

Every error path that catches an exception and builds an ``error={"reason": …}``
envelope must:
  1. Log the full exception detail server-side (verify via caplog).
  2. Return the generic friendly message ``_USER_FACING_ERROR`` in the envelope,
     NOT the raw ``str(e)``.

Run:  pytest tests/test_error_sanitization.py -v
"""

import sys
import json
import logging
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from api.main import app, _USER_FACING_ERROR

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════
#  Helper
# ═══════════════════════════════════════════════════════════════════

_DUMMY_EXC_TEXT = "cannot access local variable 'content' at line 42"


class _DummyException(Exception):
    """A distinct exception whose repr would never otherwise appear."""

    pass


def _assert_sanitized(body: dict, caplog_records: list) -> None:
    """Assert the envelope carries the friendly message and the raw exception
    string appears *nowhere* in the response body."""
    assert body["error"]["reason"] == _USER_FACING_ERROR, (
        f"Expected friendly error, got: {body['error']['reason']}"
    )
    raw_body = json.dumps(body)
    assert _DUMMY_EXC_TEXT not in raw_body, (
        f"Raw exception leaked into response body:\n{raw_body}"
    )
    # Verify at least one log record captured the dummy text
    log_text = " ".join(r.message for r in caplog_records)
    assert _DUMMY_EXC_TEXT in log_text, (
        f"Dummy exception not found in server logs:\n{log_text}"
    )


# ═══════════════════════════════════════════════════════════════════
#  1. POST /api/citation  —  Classification failure
# ═══════════════════════════════════════════════════════════════════

def test_classification_failure_sanitized(caplog):
    """Classification exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.classify_and_normalize") as mock_cls:
        mock_cls.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/citation", json={"input": "R v Jordan 2016 SCC 27"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  2. POST /api/citation  —  Search failure
# ═══════════════════════════════════════════════════════════════════

def test_search_failure_sanitized(caplog):
    """Search exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with (
        patch("api.main.classify_and_normalize") as mock_cls,
        patch("api.main.search_citation") as mock_src,
    ):
        mock_cls.return_value = {"type": "case_name"}
        mock_src.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/citation", json={"input": "R v Jordan 2016 SCC 27"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  3. POST /api/citation  —  Formatting failure (main route, single result)
# ═══════════════════════════════════════════════════════════════════

def test_formatting_failure_main_sanitized(caplog):
    """Main-route formatting exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with (
        patch("api.main.classify_and_normalize") as mock_cls,
        patch("api.main.search_citation") as mock_src,
        patch("api.main.format_citation") as mock_fmt,
    ):
        mock_cls.return_value = {"type": "case_name"}
        mock_src.return_value = [{"style_of_cause": "Test", "verified": True}]
        mock_fmt.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/citation", json={"input": "R v Jordan 2016 SCC 27"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  4. POST /api/citation  —  Formatting failure (concept route)
# ═══════════════════════════════════════════════════════════════════

def test_formatting_failure_concept_sanitized(caplog):
    """Concept-route formatting exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with (
        patch("api.main.classify_and_normalize") as mock_cls,
        patch("api.main.search_citation") as mock_src,
        patch("api.main.format_citation") as mock_fmt,
    ):
        mock_cls.return_value = {"type": "concept"}
        mock_src.return_value = [{"style_of_cause": "Duty to consult", "verified": True}]
        mock_fmt.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/citation", json={"input": "duty to consult canada"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  5. POST /api/citation/select  —  Formatting failure
# ═══════════════════════════════════════════════════════════════════

def test_formatting_failure_select_sanitized(caplog):
    """Selection-route formatting exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.format_citation") as mock_fmt:
        mock_fmt.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/citation/select", json={
            "candidates": [{"style_of_cause": "Test", "verified": True}],
            "selected_index": 0,
        })

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  6. POST /api/extract/file  —  File processing failure
# ═══════════════════════════════════════════════════════════════════

def test_file_processing_failure_sanitized(caplog):
    """File-processing exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.extract_from_file") as mock_ext:
        mock_ext.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post(
            "/api/extract/file",
            # allowlisted extension so the request reaches the extraction stage
            files={"file": ("test.docx", b"PK\x03\x04 dummy", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  7. POST /api/extract/url  —  DOI processing failure (generic Exception)
# ═══════════════════════════════════════════════════════════════════

def test_doi_processing_exception_sanitized(caplog):
    """DOI generic-Exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.format_citation") as mock_fmt:
        mock_fmt.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/extract/url", json={"doi": "10.1234/example"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  8. POST /api/extract/url  —  DOI ValueError (logged, friendly msg)
# ═══════════════════════════════════════════════════════════════════

def test_doi_value_error_sanitized(caplog):
    """DOI ValueError is logged; envelope does NOT leak the raw message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.format_citation") as mock_fmt:
        mock_fmt.side_effect = ValueError("invalid DOI: bad format 42")
        resp = client.post("/api/extract/url", json={"doi": "10.bad-doi"})

    assert resp.status_code == 200
    body = resp.json()
    # Should be the dedicated DOI message, not the raw ValueError
    assert "bad format 42" not in json.dumps(body)
    assert body["error"]["reason"] == (
        "We couldn't process this DOI. Please try again or enter the details manually."
    )
    log_text = " ".join(r.message for r in caplog.records)
    assert "bad format 42" in log_text


# ═══════════════════════════════════════════════════════════════════
#  9. POST /api/extract/url  —  ISBN processing failure (generic Exception)
# ═══════════════════════════════════════════════════════════════════

def test_isbn_processing_exception_sanitized(caplog):
    """ISBN generic-Exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with (
        patch("api.main.extract_isbn") as mock_isbn,
        patch("api.main.format_citation") as mock_fmt,
    ):
        # Prevent the DOI-fallthrough path so we hit ISBN
        mock_isbn.return_value = "978-0-1234-5678-9"
        mock_fmt.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/extract/url", json={"doi": "978-0-1234-5678-9"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
# 10. POST /api/extract/url  —  ISBN ValueError (logged, friendly msg)
# ═══════════════════════════════════════════════════════════════════

def test_isbn_value_error_sanitized(caplog):
    """ISBN ValueError is logged; envelope does NOT leak the raw message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.format_citation") as mock_fmt:
        mock_fmt.side_effect = ValueError("invalid ISBN: garbage 99")
        resp = client.post("/api/extract/url", json={"isbn": "978-0-garbage-99"})

    assert resp.status_code == 200
    body = resp.json()
    assert "garbage 99" not in json.dumps(body)
    # ValueErrors from ISBN go through the "invalid" branch → status "error"
    # with the friendly ISBN message.
    assert "ISBN" in body["error"]["reason"]
    assert "garbage" not in body["error"]["reason"]
    log_text = " ".join(r.message for r in caplog.records)
    assert "garbage 99" in log_text


# ═══════════════════════════════════════════════════════════════════
# 11. POST /api/extract/url  —  URL processing failure
# ═══════════════════════════════════════════════════════════════════

def test_url_processing_exception_sanitized(caplog):
    """URL-processing generic-Exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with (
        patch("api.main.extract_from_url") as mock_ext,
        patch("api.main.classify_document_type") as mock_cls,
        patch("api.main.format_citation") as mock_fmt,
    ):
        mock_ext.return_value = {"raw_text": "Long enough body text for URL extraction to proceed past the empty-body guard. " * 5}
        mock_cls.return_value = "news_online"
        mock_fmt.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/extract/url", json={"url": "https://example.com/article"})

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
# 12. POST /api/chat  —  Chat failure
# ═══════════════════════════════════════════════════════════════════

def test_chat_failure_sanitized(caplog):
    """Chat exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    with patch("api.main.chat_deepseek") as mock_chat:
        mock_chat.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "Hello"}],
        })

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
# 13. POST /api/feedback  —  Feedback save failure
# ═══════════════════════════════════════════════════════════════════

def test_feedback_save_failure_sanitized(caplog):
    """Feedback save exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    # Mock the file open to raise during the local JSONL write
    with patch("builtins.open") as mock_open:
        mock_open.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/feedback", json={
            "kind": "rating",
            "verdict": "up",
            "route": "case_name",
        })

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
# 14. POST /api/citation/assemble  —  Assembly failure
# ═══════════════════════════════════════════════════════════════════

def test_assembly_failure_sanitized(caplog):
    """Assembly exception is logged; envelope uses friendly message."""
    caplog.set_level(logging.WARNING)
    # The assemble route only works when SCAFFOLD_ENABLED is true — we mock its
    # dependency to force the route past the disabled gate.
    with (
        patch("api.main.SCAFFOLD_ENABLED", True),
        patch("api.main.get_field_configs") as mock_cfgs,
        patch("api.main.assemble") as mock_asm,
    ):
        mock_cfgs.return_value = {"jurisprudence": {"style_of_cause": {"type": "string"}}}
        mock_asm.side_effect = _DummyException(_DUMMY_EXC_TEXT)
        resp = client.post("/api/citation/assemble", json={
            "type": "jurisprudence",
            "fields": {"style_of_cause": "R v Jordan"},
        })

    assert resp.status_code == 200
    _assert_sanitized(resp.json(), caplog.records)


# ═══════════════════════════════════════════════════════════════════
#  Sanity: non-exception error.reason values are left intact
# ═══════════════════════════════════════════════════════════════════

def test_validation_errors_retain_their_messages():
    """Validation-only error.reason (no exception) must NOT be replaced."""
    # Empty input
    resp = client.post("/api/citation", json={"input": ""})
    assert resp.json()["error"]["reason"] == "Input cannot be empty"

    # Empty candidates
    resp = client.post("/api/citation/select", json={"candidates": [], "selected_index": 0})
    assert resp.json()["error"]["reason"] == "candidates cannot be empty"

    # Out-of-range index
    resp = client.post("/api/citation/select", json={
        "candidates": [{"style_of_cause": "A"}],
        "selected_index": 5,
    })
    assert "out of range" in resp.json()["error"]["reason"]

    # Empty messages
    resp = client.post("/api/chat", json={"messages": []})
    assert resp.json()["error"]["reason"] == "messages cannot be empty"

    # Unknown citation type (SCAFFOLD_ENABLED=true)
    with patch("api.main.SCAFFOLD_ENABLED", True):
        resp = client.post("/api/citation/assemble", json={
            "type": "nonexistent_99",
            "fields": {},
        })
    assert "Unknown citation type" in resp.json()["error"]["reason"]
