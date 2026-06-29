"""Regression tests for fast-failing unsupported URL extraction."""

import sys
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from llm_api.deepseek_api import URL_EXTRACT_FETCH_TIMEOUT, extract_from_url
from api.main import app


client = TestClient(app)


def test_url_extract_fetch_uses_tight_timeout():
    """JS-rendered/blocked pages should not wait on the old 15s timeout."""
    with patch("curl_cffi.requests.get") as mock_get:
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


def test_short_extracted_body_returns_existing_unsupported_guidance():
    """Below-threshold URL content should not fall through to slow LLM steps."""
    with (
        patch("api.main.extract_from_url") as mock_extract,
        patch("api.main.classify_document_type") as mock_classify,
        patch("api.main.format_citation") as mock_format,
    ):
        mock_extract.return_value = {"url": "https://www.ourcommons.ca/test", "raw_text": "short"}

        response = client.post("/api/extract/url", json={"url": "https://www.ourcommons.ca/test"})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "url"
    assert body["status"] == "unsupported"
    assert "Try uploading a screenshot of the page instead" in body["error"]["reason"]
    mock_classify.assert_not_called()
    mock_format.assert_not_called()
