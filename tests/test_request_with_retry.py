"""Unit tests for local_tools.utils.request_with_retry().

Run: pytest tests/test_request_with_retry.py -v
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from local_tools.utils import request_with_retry


# ═════════════════════════════════════════════════════════════════════════════
# Helper: build a mock session
# ═════════════════════════════════════════════════════════════════════════════

def _make_session(method_return_value=None, method_side_effect=None):
    """Return a MagicMock with a .request method configured."""
    session = MagicMock()
    session.request.return_value = method_return_value
    if method_side_effect is not None:
        session.request.side_effect = method_side_effect
    return session


# ═════════════════════════════════════════════════════════════════════════════
# Timeout-tuple passthrough
# ═════════════════════════════════════════════════════════════════════════════

def test_passes_timeout_tuple():
    """The helper passes timeout=(connect_timeout, read_timeout) to session.request."""
    session = _make_session(method_return_value=MagicMock())
    request_with_retry(session, "GET", "http://example.com",
                       connect_timeout=2.7, read_timeout=15)
    session.request.assert_called_once_with(
        "GET", "http://example.com",
        timeout=(2.7, 15),
    )


def test_default_read_timeout_when_omitted():
    """When no read_timeout is given, it defaults to 30, not None."""
    session = _make_session(method_return_value=MagicMock())
    # Simulate a call site that previously had no timeout at all
    request_with_retry(session, "GET", "http://example.com")
    session.request.assert_called_once_with(
        "GET", "http://example.com",
        timeout=(2.7, 30),
    )


# ═════════════════════════════════════════════════════════════════════════════
# Retry on timeout
# ═════════════════════════════════════════════════════════════════════════════

def test_retry_on_timeout_then_succeeds():
    """First call raises Timeout, second succeeds — helper returns response."""
    success_resp = MagicMock()
    success_resp.status_code = 200

    call_count = 0

    def _side_effect(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise requests.exceptions.Timeout("timed out")
        return success_resp

    session = _make_session(method_side_effect=_side_effect)
    result = request_with_retry(session, "GET", "http://example.com",
                                read_timeout=15, retries=1, backoff=0.01)

    assert result is success_resp
    assert call_count == 2


# ═════════════════════════════════════════════════════════════════════════════
# All attempts fail (connection error)
# ═════════════════════════════════════════════════════════════════════════════

def test_all_attempts_fail_raises():
    """Every call raises ConnectionError — helper re-raises after retries+1 attempts."""
    session = _make_session(
        method_side_effect=requests.exceptions.ConnectionError("refused"),
    )
    with pytest.raises(requests.exceptions.ConnectionError):
        request_with_retry(session, "GET", "http://example.com",
                           read_timeout=15, retries=2, backoff=0.01)

    # Called retries+1 = 3 times
    assert session.request.call_count == 3


# ═════════════════════════════════════════════════════════════════════════════
# Non-retryable exceptions propagate immediately
# ═════════════════════════════════════════════════════════════════════════════

def test_non_retryable_exception_propagates():
    """HTTPError propagates immediately with exactly one attempt (no retry)."""
    session = _make_session(
        method_side_effect=requests.exceptions.HTTPError("400 Bad Request"),
    )
    with pytest.raises(requests.exceptions.HTTPError):
        request_with_retry(session, "GET", "http://example.com",
                           read_timeout=15, retries=1, backoff=0.01)

    assert session.request.call_count == 1, (
        "Should not retry on HTTPError"
    )


def test_value_error_propagates():
    """ValueError propagates immediately with exactly one attempt (no retry)."""
    session = _make_session(
        method_side_effect=ValueError("bad value"),
    )
    with pytest.raises(ValueError):
        request_with_retry(session, "GET", "http://example.com",
                           read_timeout=15, retries=1, backoff=0.01)

    assert session.request.call_count == 1, (
        "Should not retry on ValueError"
    )


# ═════════════════════════════════════════════════════════════════════════════
# POST request with kwargs (json, headers)
# ═════════════════════════════════════════════════════════════════════════════

def test_post_with_kwargs():
    """POST call with json and headers passes them through unchanged."""
    session = _make_session(method_return_value=MagicMock())
    request_with_retry(
        session, "POST", "http://api.example.com/data",
        json={"key": "value"},
        headers={"Authorization": "Bearer xyz"},
        read_timeout=30,
    )
    session.request.assert_called_once_with(
        "POST", "http://api.example.com/data",
        json={"key": "value"},
        headers={"Authorization": "Bearer xyz"},
        timeout=(2.7, 30),
    )


# ═════════════════════════════════════════════════════════════════════════════
# POST with retries=0: no retry on Timeout (duplicate-LLM-charge protection)
# ═════════════════════════════════════════════════════════════════════════════

def test_post_retries_zero_no_retry_on_timeout():
    """POST with retries=0 calls session.request exactly once, exception propagates."""
    session = _make_session(
        method_side_effect=requests.exceptions.Timeout("read timeout"),
    )
    with pytest.raises(requests.exceptions.Timeout):
        request_with_retry(session, "POST", "http://api.example.com/data",
                           read_timeout=30, retries=0, backoff=0.01)

    assert session.request.call_count == 1, (
        "Should not retry POST with retries=0"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Regression: browse_legislation_in_database unchanged behavior
# ═════════════════════════════════════════════════════════════════════════════

@patch("os.environ.get", return_value="test-api-key")
@patch("local_tools.canlii_api.canlii_session.request")
@patch("profiling.timing.ENABLED", False)
def test_browse_legislation_in_database_unchanged(mock_request, _mock_env):
    """browse_legislation_in_database still returns the same data via the new helper."""
    from local_tools.canlii_api import browse_legislation_in_database

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"legislations": [{"title": "Test Act"}]}
    mock_request.return_value = mock_resp

    result = browse_legislation_in_database("abs")

    assert result == {"legislations": [{"title": "Test Act"}]}
    mock_resp.raise_for_status.assert_called_once()
