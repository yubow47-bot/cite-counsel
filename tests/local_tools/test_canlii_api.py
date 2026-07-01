"""Unit tests for canlii_api.py functions.

Run: pytest tests/local_tools/test_canlii_api.py -v
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.canlii_api import browse_legislation_in_database


# ═════════════════════════════════════════════════════════════════════════════
# Regression: Bug 1 — t0 → _http_t0 in browse_legislation_in_database()
# ═════════════════════════════════════════════════════════════════════════════

def test_browse_legislation_in_database_returns_json_on_success():
    """browse_legislation_in_database returns response.json() without raising NameError."""
    from local_tools import timing_util as timing
    timing.start()  # fresh report so we can inspect a2aj_calls

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"legislations": [{"title": "Test Act", "citation": "RSA 2000, c T-1"}]}
    mock_get = MagicMock(return_value=mock_resp)

    with patch("os.environ.get", return_value="test-api-key"), \
         patch("local_tools.canlii_api.canlii_session.request", mock_get), \
         patch("profiling.timing.ENABLED", False):
        result = browse_legislation_in_database("abs")

    assert result == {"legislations": [{"title": "Test Act", "citation": "RSA 2000, c T-1"}]}
    # Verify it went through the success path, not the error path
    mock_resp.raise_for_status.assert_called_once()

    # Timing-sanity: the elapsed value recorded via add_a2aj must be a
    # small positive float (sub-second HTTP mock), not an epoch-scale number
    assert len(timing.report().a2aj_calls) >= 1
    elapsed = timing.report().a2aj_calls[0][1]
    assert 0 <= elapsed < 60, (
        f"Expected small positive elapsed (seconds), got {elapsed} — "
        "likely a mixed-clock bug (perf_counter vs time.time)"
    )


def test_browse_legislation_in_database_empty_database_id():
    """Empty database_id returns error dict immediately, no API call."""
    mock_get = MagicMock()
    with patch("local_tools.canlii_api.canlii_session.request", mock_get):
        result = browse_legislation_in_database("")
    assert result == {"error": "database_id required"}
    mock_get.assert_not_called()


def test_browse_legislation_in_database_whitespace_database_id():
    """Whitespace-only database_id returns error dict immediately, no API call."""
    mock_get = MagicMock()
    with patch("local_tools.canlii_api.canlii_session.request", mock_get):
        result = browse_legislation_in_database("   ")
    assert result == {"error": "database_id required"}
    mock_get.assert_not_called()


def test_browse_legislation_in_database_no_api_key():
    """Missing CANLII_API_KEY returns error dict immediately, no API call."""
    mock_get = MagicMock()
    with patch("os.environ.get", return_value=None), \
         patch("local_tools.canlii_api.canlii_session.request", mock_get):
        result = browse_legislation_in_database("abs")
    assert result == {"error": "CANLII_API_KEY not configured"}
    mock_get.assert_not_called()


def test_browse_legislation_in_database_http_error():
    """HTTP error (e.g. 500) returns error dict, not an exception."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = __import__("requests").exceptions.HTTPError("500 Server Error")
    mock_get = MagicMock(return_value=mock_resp)

    with patch("os.environ.get", return_value="test-api-key"), \
         patch("local_tools.canlii_api.canlii_session.request", mock_get), \
         patch("local_tools.timing_util.ENABLE_TIMING", False), \
         patch("profiling.timing.ENABLED", False):
        result = browse_legislation_in_database("abs")

    assert "error" in result
    assert "Legal database lookup failed" in result["error"]
