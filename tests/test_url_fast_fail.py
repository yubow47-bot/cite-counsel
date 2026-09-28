"""Regression tests for fast-failing unsupported URL extraction."""

import sys
import time
from unittest.mock import patch

sys.path.insert(0, ".")

from llm_api.deepseek_api import URL_EXTRACT_FETCH_TIMEOUT, extract_from_url


def test_url_extract_fetch_uses_tight_timeout():
    """JS-rendered/blocked pages should not wait on the old 15s timeout."""
    with patch("curl_cffi.requests.get") as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry", side_effect=TimeoutError("fallback also fails")):
        mock_get.side_effect = TimeoutError("simulated timeout")

        start = time.perf_counter()
        result = extract_from_url("https://www.ourcommons.ca/documentviewer/en/44-1/house/sitting-372/hansard")
        elapsed = time.perf_counter() - start

    assert "error" in result
    assert elapsed < URL_EXTRACT_FETCH_TIMEOUT + 0.5
    assert mock_get.call_args.kwargs["timeout"] == URL_EXTRACT_FETCH_TIMEOUT


def test_supported_static_url_still_extracts_content():
    """A normal static HTML page still returns extracted body text."""
    html = """
    <html>
      <head><title>Static Test Page</title></head>
      <body>
        <article>
          <h1>Static Test Page</h1>
          <p>This static page has enough plain body content for extraction. It
          does not require JavaScript rendering and should remain supported by
          the URL extraction path.</p>
        </article>
      </body>
    </html>
    """
    with patch("llm_api.deepseek_api.fetch_html", return_value=html):
        result = extract_from_url("https://example.com/static")

    assert "error" not in result
    assert "Static Test Page" in (result.get("page_title") or result.get("raw_text") or "")
    assert len(result.get("raw_text", "").strip()) >= 50
