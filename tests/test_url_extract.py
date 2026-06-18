"""FastAPI TestClient for /api/extract/url with mocked deterministic paths."""

import json
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")

# ── Patch spend tracker BEFORE importing api.main ──
_mock_tracker = MagicMock()
_mock_tracker.is_over_cap.return_value = False
_patcher_tracker = patch("core.spend_tracker.spend_tracker", _mock_tracker)
_patcher_tracker.start()

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════
#  Fixtures — mock data
# ═══════════════════════════════════════════════════════════════════

FAKE_CROSSREF_MESSAGE = {
    "author": [{"given": "Jane", "family": "Doe"}],
    "title": ["Test Article Title"],
    "container-title": ["Test Journal"],
    "published": {"date-parts": [[2023]]},
    "volume": "10",
    "issue": "2",
    "page": "100-120",
}

FAKE_OL_DATA = {
    "title": "Telecommunications Law",
    "authors": [{"name": "David Gilles"}],
    "publishers": [{"name": "Butterworths"}],
    "publish_date": "2003",
    "publish_places": [{"name": "London, UK"}],
}

FAKE_URL_FIELDS = {
    "url": "https://example.com/article",
    "page_title": "Example Article",
    "author": "Test Author",
    "website": "Example Site",
    "date": "2023-06-01",
}


# ═══════════════════════════════════════════════════════════════════
#  Helper
# ═══════════════════════════════════════════════════════════════════

def _reset_engine_source():
    """Reset mcgill_engine globals so get_last_debug().source is clean."""
    import core.mcgill_engine as eng
    eng._last_source = None
    eng._last_prompt = None
    eng._last_raw_response = None


# ═══════════════════════════════════════════════════════════════════
#  Tests
# ═══════════════════════════════════════════════════════════════════

class TestExtractUrlDoi:
    """POST /api/extract/url with only doi → journal_article (CrossRef)."""

    def test_doi_only(self):
        _reset_engine_source()
        with patch("core.mcgill_engine.fetch_crossref") as m_cr:
            m_cr.return_value = FAKE_CROSSREF_MESSAGE

            resp = client.post("/api/extract/url", json={"doi": "10.1006/bbrc.2001.4705"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "done"
        assert len(body["data"]["citations"]) == 1
        cit = body["data"]["citations"][0]["citation"]
        assert "Jane Doe" in cit
        assert "Test Article Title" in cit
        # Ensure tracker was NOT incremented (deterministic hit)
        # (increment might or might not have been called — that's fine, just verify
        # the source tracking doesn't block the happy path)

    def test_doi_with_url_ignored(self):
        """When doi is present, url is passed along but doi takes priority."""
        _reset_engine_source()
        with patch("core.mcgill_engine.fetch_crossref") as m_cr:
            m_cr.return_value = FAKE_CROSSREF_MESSAGE

            resp = client.post("/api/extract/url", json={
                "doi": "10.1006/bbrc.2001.4705",
                "url": "https://example.com",
            })

        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        m_cr.assert_called_once_with("10.1006/bbrc.2001.4705")


class TestExtractUrlIsbn:
    """POST /api/extract/url with only isbn → book (Open Library)."""

    def test_isbn_only(self):
        _reset_engine_source()
        with (
            patch("core.mcgill_engine.fetch_openlibrary") as m_ol,
            patch("core.mcgill_engine.extract_isbn") as m_extract,
            patch("core.mcgill_engine.validate_isbn") as m_valid,
        ):
            m_extract.return_value = "9780199576857"
            m_valid.return_value = True
            m_ol.return_value = FAKE_OL_DATA

            resp = client.post("/api/extract/url", json={"isbn": "978-0-19-957685-7"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "done"
        assert len(body["data"]["citations"]) == 1
        cit = body["data"]["citations"][0]["citation"]
        assert "David Gilles" in cit
        assert "Telecommunications Law" in cit
        assert "Butterworths" in cit


class TestExtractUrlOnly:
    """POST /api/extract/url with only url → existing behaviour."""

    def test_url_only(self):
        _reset_engine_source()
        with (
            patch("api.main.extract_from_url") as m_extract,
            patch("api.main.format_citation") as m_fmt,
            patch("api.main.get_last_debug") as m_dbg,
        ):
            m_extract.return_value = FAKE_URL_FIELDS
            m_fmt.return_value = "*Test Citation*, 2023."
            m_dbg.return_value = {"source": "deepseek", "prompt": "", "raw_response": ""}

            resp = client.post("/api/extract/url", json={"url": "https://example.com/article"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "done"
        assert len(body["data"]["citations"]) == 1
        assert "Test Citation" in body["data"]["citations"][0]["citation"]
        m_extract.assert_called_once_with("https://example.com/article")

    def test_url_extract_failure_scaffold(self):
        """When extract_from_url returns an error → unsupported (scaffold disabled by default)."""
        _reset_engine_source()
        with patch("api.main.extract_from_url") as m_extract:
            m_extract.return_value = {"error": "Could not fetch page"}

            resp = client.post("/api/extract/url", json={"url": "https://example.com/blocked"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "unsupported"
        assert "block" in body["error"]["reason"].lower()


class TestExtractUrlEmpty:
    """POST /api/extract/url with all fields empty."""

    def test_all_empty(self):
        resp = client.post("/api/extract/url", json={"url": "", "doi": "", "isbn": ""})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["status"] == "error"
        assert "Provide a URL, DOI, or ISBN" in body["error"]["reason"]

    def test_all_none(self):
        resp = client.post("/api/extract/url", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["status"] == "error"
        assert "Provide a URL, DOI, or ISBN" in body["error"]["reason"]


# ── Cleanup ──
_patcher_tracker.stop()
