"""Unit tests for extract_from_url() in web_extract.py.

Run: pytest tests/test_extract_from_url.py -v
"""

import json
import os
import sys
import pytest
import requests
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.web_extract import extract_from_url, fetch_html


@pytest.fixture(autouse=True)
def _fake_dns():
    """Resolve all hostnames to a public IP so tests never touch real DNS
    (fetch_html now validates every URL through the SSRF guard)."""
    def fake_getaddrinfo(hostname, port=None, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]
    with patch("socket.getaddrinfo", fake_getaddrinfo):
        yield


# ═════════════════════════════════════════════════════════════════════════════
# Regression: Bug 2b — result unbound when trafilatura.extract raises
# ═════════════════════════════════════════════════════════════════════════════

def test_extract_from_url_trafilatura_extract_raises():
    """trafilatura.extract raises -> error dict, no UnboundLocalError."""
    with patch("local_tools.web_extract.fetch_html", return_value="<html><body>test</body></html>"), \
         patch("trafilatura.extract", side_effect=ValueError("parse error")):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "We couldn't read the content of this page" in result["error"]


def test_extract_from_url_trafilatura_extract_raises_runtime_error():
    """trafilatura.extract raises RuntimeError -> error dict, no crash."""
    with patch("local_tools.web_extract.fetch_html", return_value="<html><body>test</body></html>"), \
         patch("trafilatura.extract", side_effect=RuntimeError("unexpected error")):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "We couldn't read the content of this page" in result["error"]


# ═════════════════════════════════════════════════════════════════════════════
# Existing paths (regression guard)
# ═════════════════════════════════════════════════════════════════════════════

def test_extract_from_url_fetch_html_fails():
    """fetch_html returns None -> error dict (no longer asserts a specific cause)."""
    with patch("local_tools.web_extract.fetch_html", return_value=None):
        result = extract_from_url("https://example.com/blocked")

    assert "error" in result
    # The error message must NOT claim a specific cause
    assert "blocked" not in result["error"].lower()
    assert "couldn't fetch" in result["error"].lower()


def test_extract_from_url_trafilatura_returns_none():
    """trafilatura.extract returns None -> extraction failure error dict."""
    with patch("local_tools.web_extract.fetch_html", return_value="<html><body>test</body></html>"), \
         patch("trafilatura.extract", return_value=None):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "could not extract" in result["error"].lower()


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: fetch_html fallback (curl_cffi -> plain requests)
# ═════════════════════════════════════════════════════════════════════════════

_UA_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def test_fetch_html_fallback_succeeds():
    """curl_cffi times out, fallback succeeds -> returns fallback HTML."""
    mock_curl = MagicMock(side_effect=TimeoutError("curl_cffi timed out"))

    mock_fb_resp = MagicMock()
    mock_fb_resp.text = "<html><body>fallback content</body></html>"
    mock_fallback = MagicMock(return_value=mock_fb_resp)

    with patch("curl_cffi.requests.get", mock_curl), \
         patch("local_tools.web_extract.request_with_retry", mock_fallback):
        html = fetch_html("https://example.com/article", timeout=8)

    assert html == "<html><body>fallback content</body></html>", (
        f"Expected fallback HTML, got {html!r}"
    )
    # Fallback was called with the expected User-Agent
    mock_fallback.assert_called_once()
    call_kwargs = mock_fallback.call_args[1]
    assert call_kwargs.get("headers", {}).get("User-Agent") == _UA_FALLBACK


def test_fetch_html_both_fail():
    """Both curl_cffi and fallback fail -> returns None."""
    mock_curl = MagicMock(side_effect=TimeoutError("curl_cffi timed out"))
    mock_fallback = MagicMock(side_effect=ConnectionError("fallback failed"))

    with patch("curl_cffi.requests.get", mock_curl), \
         patch("local_tools.web_extract.request_with_retry", mock_fallback):
        html = fetch_html("https://example.com/article", timeout=8)

    assert html is None, f"Expected None, got {html!r}"
    mock_fallback.assert_called_once()


def test_fetch_html_curl_succeeds_no_fallback():
    """curl_cffi succeeds directly -> fallback is NOT attempted."""
    mock_curl_resp = MagicMock()
    mock_curl_resp.text = "<html><body>primary content</body></html>"
    mock_curl = MagicMock(return_value=mock_curl_resp)
    mock_fallback = MagicMock()

    with patch("curl_cffi.requests.get", mock_curl), \
         patch("local_tools.web_extract.request_with_retry", mock_fallback):
        html = fetch_html("https://example.com/article", timeout=8)

    assert html == "<html><body>primary content</body></html>", (
        f"Expected primary HTML, got {html!r}"
    )
    # Fallback must NOT be called when primary succeeds
    mock_fallback.assert_not_called()


def test_fetch_html_fallback_http_error_returns_none():
    """Fallback returns a response with non-2xx status -> fetch_html returns None."""
    mock_curl = MagicMock(side_effect=TimeoutError("curl_cffi timed out"))

    mock_fb_resp = MagicMock()
    mock_fb_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("403 Forbidden")
    mock_fallback = MagicMock(return_value=mock_fb_resp)

    with patch("curl_cffi.requests.get", mock_curl), \
         patch("local_tools.web_extract.request_with_retry", mock_fallback):
        html = fetch_html("https://example.com/article", timeout=8)

    assert html is None, (
        f"Expected None when fallback returns HTTP error, got {html!r}"
    )
    mock_fallback.assert_called_once()
    # The error page body must NOT be returned as valid HTML
    mock_fb_resp.raise_for_status.assert_called_once()
    mock_fb_resp.close.assert_called_once()


def test_extract_from_url_error_does_not_claim_blocked():
    """Error message no longer asserts a specific blocked cause."""
    with patch("local_tools.web_extract.fetch_html", return_value=None):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "blocked" not in result["error"].lower(), (
        "Error message must not claim a specific cause"
    )
    assert "couldn't fetch" in result["error"].lower(), (
        "Error message should acknowledge uncertainty"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  PDF URL short-circuit (Item: extract_from_url PDF guard)
# ═════════════════════════════════════════════════════════════════════════════

def test_extract_from_url_pdf_suffix_short_circuits():
    """URL ending in .pdf returns error immediately; fetch_html is NOT called."""
    with patch("local_tools.web_extract.fetch_html") as mock_fetch:
        result = extract_from_url("https://example.com/document.pdf")

    assert "error" in result
    assert "reliably read a direct PDF link" in result["error"]
    assert result["url"] == "https://example.com/document.pdf"
    mock_fetch.assert_not_called()


def test_extract_from_url_pdf_suffix_case_insensitive():
    """URL ending in .PDF (uppercase) short-circuits the same way."""
    with patch("local_tools.web_extract.fetch_html") as mock_fetch:
        result = extract_from_url("https://example.com/report.PDF")

    assert "error" in result
    assert "reliably read a direct PDF link" in result["error"]
    mock_fetch.assert_not_called()


def test_extract_from_url_pdf_suffix_with_query_string():
    """Query string is stripped before the suffix check; short-circuits."""
    with patch("local_tools.web_extract.fetch_html") as mock_fetch:
        result = extract_from_url("https://example.com/file.pdf?download=true")

    assert "error" in result
    assert "reliably read a direct PDF link" in result["error"]
    mock_fetch.assert_not_called()


def test_extract_from_url_non_pdf_url_unaffected():
    """Non-PDF URLs still go through fetch_html and trafilatura as before."""
    fake_trafilatura_json = json.dumps({
        "title": "Normal Article",
        "author": "Author Name",
        "date": "2023-06-01",
        "hostname": "example.com",
        "raw_text": (
            "This is a normal article with enough text to pass the empty-body "
            "guard threshold of fifty characters in the raw text field."
        ),
    })
    with (
        patch("local_tools.web_extract.fetch_html", return_value="<html><body>ok</body></html>") as mock_fetch,
        patch("trafilatura.extract", return_value=fake_trafilatura_json),
    ):
        result = extract_from_url("https://example.com/article")

    assert "error" not in result
    assert result["page_title"] == "Normal Article"
    assert result["author"] == "Author Name"
    mock_fetch.assert_called_once()
