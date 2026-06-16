"""FastAPI TestClient for concept-route needs_selection fix.

Ensures _handle_concept mirrors case_name: multiple results → needs_selection,
single result → done, empty → needs_input.
"""

import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")

# ── Patch tracker BEFORE importing api.main ──
_mock_tracker = MagicMock()
_mock_tracker.check.return_value = (True, None)
_patcher_tracker = patch("api.spend_tracker.tracker", _mock_tracker)
_patcher_tracker.start()

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


FAKE_CANDIDATES = [
    {"name": "Duty to Consult", "style_of_cause": "Haida Nation v British Columbia",
     "neutral_citation": "2004 SCC 73", "url": "https://canlii.ca/t/1gh6g", "verified": True},
    {"name": "Duty to Consult", "style_of_cause": "Taku River Tlingit First Nation v BC",
     "neutral_citation": "2004 SCC 74", "url": "https://canlii.ca/t/1gh6h", "verified": True},
    {"name": "Duty to Consult", "style_of_cause": "Mikisew Cree First Nation v Canada",
     "neutral_citation": "2018 SCC 40", "url": "https://canlii.ca/t/j3g5f", "verified": True},
]

SINGLE_RESULT = [FAKE_CANDIDATES[0]]


def _reset_caches():
    """Clear any module-level caches between tests."""
    import api.main as m
    # no-op for now


class TestConceptNeedsSelection:
    """POST /api/citation with concept route → multiple candidates."""

    def test_multiple_candidates_returns_needs_selection(self):
        """3 concept results → status=needs_selection, 3 candidates with display."""
        with (
            patch("api.main.classify_and_normalize") as m_cls,
            patch("api.main.search_citation") as m_search,
            patch("api.main.format_citation") as m_fmt,
        ):
            m_cls.return_value = {"type": "concept", "normalized": "duty to consult"}
            m_search.return_value = FAKE_CANDIDATES

            resp = client.post("/api/citation", json={"input": "duty to consult"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "needs_selection"
        assert body["route"] == "concept"
        assert len(body["data"]["candidates"]) == 3
        for c in body["data"]["candidates"]:
            assert "display" in c
        # format_citation must NOT be called (format deferred to select)
        m_fmt.assert_not_called()

    def test_single_result_returns_done(self):
        """1 concept result → status=done, citation formatted."""
        with (
            patch("api.main.classify_and_normalize") as m_cls,
            patch("api.main.search_citation") as m_search,
            patch("api.main.format_citation") as m_fmt,
            patch("api.main.get_last_debug") as m_dbg,
        ):
            m_cls.return_value = {"type": "concept", "normalized": "duty to consult"}
            m_search.return_value = SINGLE_RESULT
            m_fmt.return_value = "*Haida Nation v British Columbia*, 2004 SCC 73."
            m_dbg.return_value = {"source": "deepseek", "prompt": "", "raw_response": ""}

            resp = client.post("/api/citation", json={"input": "duty to consult"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "done"
        assert body["route"] == "concept"
        assert len(body["data"]["citations"]) == 1
        assert "Haida Nation" in body["data"]["citations"][0]["citation"]
        m_fmt.assert_called_once()

    def test_empty_results_returns_needs_input(self):
        """0 concept results → status=needs_input (scaffold)."""
        with (
            patch("api.main.classify_and_normalize") as m_cls,
            patch("api.main.search_citation") as m_search,
        ):
            m_cls.return_value = {"type": "concept", "normalized": "duty to consult"}
            m_search.return_value = []

            resp = client.post("/api/citation", json={"input": "duty to consult"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "needs_input"
        assert body["route"] == "concept"
        assert "type" in body["data"]


# ── Cleanup ──
_patcher_tracker.stop()
