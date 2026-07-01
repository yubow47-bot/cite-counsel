"""Unit tests for extract_from_url() in deepseek_api.py.

Run: pytest tests/test_extract_from_url.py -v
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_api.deepseek_api import extract_from_url


# ═════════════════════════════════════════════════════════════════════════════
# Regression: Bug 2b — result unbound when trafilatura.extract raises
# ═════════════════════════════════════════════════════════════════════════════

def test_extract_from_url_trafilatura_extract_raises():
    """trafilatura.extract raises -> error dict, no UnboundLocalError."""
    with patch("llm_api.deepseek_api.fetch_html", return_value="<html><body>test</body></html>"), \
         patch("trafilatura.extract", side_effect=ValueError("parse error")):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "Content extraction failed" in result["error"]


def test_extract_from_url_trafilatura_extract_raises_runtime_error():
    """trafilatura.extract raises RuntimeError -> error dict, no crash."""
    with patch("llm_api.deepseek_api.fetch_html", return_value="<html><body>test</body></html>"), \
         patch("trafilatura.extract", side_effect=RuntimeError("unexpected error")):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "Content extraction failed" in result["error"]


# ═════════════════════════════════════════════════════════════════════════════
# Existing paths (regression guard)
# ═════════════════════════════════════════════════════════════════════════════

def test_extract_from_url_fetch_html_fails():
    """fetch_html returns None -> blocked error dict."""
    with patch("llm_api.deepseek_api.fetch_html", return_value=None):
        result = extract_from_url("https://example.com/blocked")

    assert "error" in result
    assert "blocked" in result["error"].lower()


def test_extract_from_url_trafilatura_returns_none():
    """trafilatura.extract returns None -> extraction failure error dict."""
    with patch("llm_api.deepseek_api.fetch_html", return_value="<html><body>test</body></html>"), \
         patch("trafilatura.extract", return_value=None):
        result = extract_from_url("https://example.com/article")

    assert "error" in result
    assert "could not extract" in result["error"].lower()
