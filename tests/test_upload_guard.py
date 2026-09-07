"""Tests for the /api/extract/file upload gate.

Covers: extension allowlist (unsupported types rejected before any parsing or
LLM call), chunked upload size limit, magic-byte content check, and that
allowlisted files with plausible magic reach the extractor.

Run: pytest tests/test_upload_guard.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


# ═════════════════════════════════════════════════════════════════════════════
#  Extension allowlist
# ═════════════════════════════════════════════════════════════════════════════

def test_upload_txt_rejected_as_unsupported():
    with patch("api.main.extract_from_file") as mock_ext:
        resp = client.post(
            "/api/extract/file",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
    body = resp.json()
    assert body["status"] == "unsupported"
    assert "Unsupported file type" in body["error"]["reason"]
    mock_ext.assert_not_called()  # must never reach the extractor / LLM


def test_upload_no_extension_rejected():
    with patch("api.main.extract_from_file") as mock_ext:
        resp = client.post("/api/extract/file", files={"file": ("README", b"x", "application/octet-stream")})
    assert resp.json()["status"] == "unsupported"
    mock_ext.assert_not_called()


def test_upload_pathological_suffix_rejected_no_500():
    """Absurd suffixes must be rejected cleanly — never an unhandled 500.

    A 500-char suffix still passes multipart parsing and must hit the
    extension allowlist (200 + unsupported); a 5-KB suffix is dropped by the
    multipart layer itself (400).  Both are safe, neither may be a 500.
    """
    for name in ("a." + "b" * 500, "a." + "b" * 5000):
        with patch("api.main.extract_from_file") as mock_ext:
            resp = client.post(
                "/api/extract/file",
                files={"file": (name, b"x", "application/octet-stream")},
            )
            assert resp.status_code in (200, 400), f"{name[:20]}… → {resp.status_code}"
            if resp.status_code == 200:
                assert resp.json()["status"] == "unsupported"
            mock_ext.assert_not_called()


def test_upload_executable_disguise_rejected():
    with patch("api.main.extract_from_file") as mock_ext:
        resp = client.post(
            "/api/extract/file",
            files={"file": ("payload.pdf", b"MZ\x90\x00 fake exe", "application/pdf")},
        )
    body = resp.json()
    assert body["status"] == "unsupported"
    assert "doesn't appear to be a valid PDF" in body["error"]["reason"]
    mock_ext.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
#  Size limit (chunked read)
# ═════════════════════════════════════════════════════════════════════════════

def test_upload_oversized_rejected_before_extraction():
    with patch("api.main.MAX_UPLOAD_MB", 1), \
         patch("api.main.extract_from_file") as mock_ext:
        resp = client.post(
            "/api/extract/file",
            # 1.2 MB against a 1 MB cap → rejected mid-stream
            files={"file": ("doc.pdf", b"%PDF-1.4" + b"\x00" * (1200 * 1024), "application/pdf")},
        )
    body = resp.json()
    assert body["status"] == "error"
    assert "File too large" in body["error"]["reason"]
    mock_ext.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
#  Gate pass-through: plausible files reach the extractor
# ═════════════════════════════════════════════════════════════════════════════

def test_upload_docx_with_zip_magic_reaches_extractor():
    with patch("api.main.extract_from_file", MagicMock(return_value={"error": "empty document"})) as mock_ext:
        resp = client.post(
            "/api/extract/file",
            files={"file": ("doc.docx", b"PK\x03\x04 rest-of-zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    body = resp.json()
    assert mock_ext.call_count == 1
    assert body["status"] == "unsupported"  # from the mocked extractor error


def test_upload_pdf_with_pdf_magic_reaches_extractor():
    with patch("api.main.extract_from_file", MagicMock(return_value={"error": "empty document"})) as mock_ext:
        resp = client.post(
            "/api/extract/file",
            files={"file": ("doc.pdf", b"%PDF-1.4\n...", "application/pdf")},
        )
    assert mock_ext.call_count == 1
    assert resp.json()["status"] == "unsupported"


def test_upload_jpeg_with_jpeg_magic_reaches_extractor():
    with patch("api.main.extract_from_file", MagicMock(return_value={"error": "no text"})) as mock_ext:
        resp = client.post(
            "/api/extract/file",
            files={"file": ("pic.jpg", b"\xff\xd8\xff\xe0 rest", "image/jpeg")},
        )
    assert mock_ext.call_count == 1
    assert resp.json()["status"] == "unsupported"


def test_upload_png_magic_mismatch_rejected():
    with patch("api.main.extract_from_file") as mock_ext:
        resp = client.post(
            "/api/extract/file",
            files={"file": ("pic.png", b"GIF89a not a png", "image/png")},
        )
    assert resp.json()["status"] == "unsupported"
    mock_ext.assert_not_called()
