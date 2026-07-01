"""Tests for /api/warmup endpoint — connection warm-up on page load.

Asserts:
1. Normal case: all providers return HTTP 200 → ``warmed`` lists all 7.
2. Fault isolation: one provider raising → others unaffected, correct summary.
3. Rate-limit exemption: rapid calls to /api/warmup are never 429'd.

Run:  pytest tests/test_warmup.py -v
"""

import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

_PROVIDER_NAMES = [
    "legisinfo", "a2aj", "canlii", "crossref",
    "openlibrary", "deepseek", "gemini",
]


def _mock_response(status: int = 200, text: str = "ok") -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.text = text
    return m


class TestWarmupEndpoint:
    """Tests for GET /api/warmup."""

    def test_all_providers_succeed(self):
        """All 7 providers return 200 → warmed contains all 7, failed empty."""
        with patch("api.main.request_with_retry", return_value=_mock_response()):
            resp = client.get("/api/warmup")

        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["warmed"]) == sorted(_PROVIDER_NAMES)
        assert body["failed"] == []

    def test_single_provider_fails_others_unaffected(self):
        """One provider raising does not block the other six."""
        def _side_effect(session, method, url, **kw):
            url_str = url if isinstance(url, str) else str(url)
            if "api.a2aj.ca" in url_str:
                raise ConnectionError("a2aj unreachable")
            return _mock_response()

        with patch("api.main.request_with_retry", side_effect=_side_effect):
            resp = client.get("/api/warmup")

        assert resp.status_code == 200
        body = resp.json()
        assert "a2aj" in [f["provider"] for f in body["failed"]]
        assert len(body["warmed"]) == 6
        assert "a2aj" not in body["warmed"]

    def test_all_providers_fail(self):
        """All providers fail → warmed empty, failed lists all 7."""
        with patch("api.main.request_with_retry", side_effect=TimeoutError("timeout")):
            resp = client.get("/api/warmup")

        assert resp.status_code == 200
        body = resp.json()
        assert body["warmed"] == []
        assert len(body["failed"]) == 7

    def test_some_succeed_some_fail(self):
        """Mixed results — exactly those that raise appear in failed."""
        call_count = [0]

        def _side_effect(session, method, url, **kw):
            url_str = url if isinstance(url, str) else str(url)
            idx = call_count[0]
            call_count[0] += 1
            if idx in (0, 3, 5):  # first, fourth, sixth call
                raise RuntimeError("boom")
            return _mock_response()

        with patch("api.main.request_with_retry", side_effect=_side_effect):
            resp = client.get("/api/warmup")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["warmed"]) == 4
        assert len(body["failed"]) == 3

    def test_http_error_status_still_counts_as_warmed(self):
        """Non-2xx response does not raise — the connection was made."""
        with patch("api.main.request_with_retry", return_value=_mock_response(status=503)):
            resp = client.get("/api/warmup")

        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["warmed"]) == sorted(_PROVIDER_NAMES)
        assert body["failed"] == []

    def test_response_shape(self):
        """Response has exactly the expected keys."""
        with patch("api.main.request_with_retry", return_value=_mock_response()):
            resp = client.get("/api/warmup")

        body = resp.json()
        assert set(body.keys()) == {"warmed", "failed"}
        assert isinstance(body["warmed"], list)
        assert isinstance(body["failed"], list)


class TestWarmupRateLimitExemption:
    """/api/warmup must not be blocked by rate_limit_middleware."""

    def test_rapid_calls_not_rate_limited(self):
        """30 rapid calls to /api/warmup all return 200, never 429."""
        with patch("api.main.request_with_retry", return_value=_mock_response()):
            for _ in range(30):
                resp = client.get("/api/warmup")
                assert resp.status_code == 200, (
                    f"Expected 200, got {resp.status_code}: {resp.text}"
                )

    def test_free_path_alongside_real_endpoint(self):
        """Rapid /api/warmup does NOT exhaust the rate limiter for real endpoints."""
        # Fire 20 warmup calls, then check that a health endpoint still works
        with patch("api.main.request_with_retry", return_value=_mock_response()):
            for _ in range(20):
                client.get("/api/warmup")

        # Health check is also in free_paths, but this confirms we didn't
        # corrupt middleware state
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
