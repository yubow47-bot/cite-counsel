"""Deterministic tests for DeepSeek spend tracking in _call_deepseek().

AC1 — spend is recorded with correct args
AC2 — tracking failure does not break the response
AC3 — missing usage key does not crash
AC4 — regression: all existing tests still pass (run via pytest in CI)
"""

import os
import sys
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path so imports like "from core.spend_tracker" work
_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

# Must set API key before importing the module under test (it reads env at import).
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-placeholder")

from llm_api.deepseek_api import ask_deepseek


def _mock_response(data: dict) -> MagicMock:
    """Build a fake requests.Response that mirrors the real API response shape."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


class TestDeepSeekSpendTracking:

    # ── AC1: spend is recorded ──────────────────────────────────────

    def test_ac1_spend_recorded(self):
        """record_cost is called once with (deepseek, model, prompt_tokens, completion_tokens)."""
        fake_data = {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            with patch("core.spend_tracker.spend_tracker.record_cost") as m_record:
                result = ask_deepseek("x")

        assert result == "x"
        m_record.assert_called_once_with("deepseek", "deepseek-v4-flash", 1000, 500)

    def test_ac1_spend_recorded_custom_model(self):
        """A non-default model string is forwarded to record_cost."""
        fake_data = {
            "choices": [{"message": {"content": "y"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            with patch("core.spend_tracker.spend_tracker.record_cost") as m_record:
                result = ask_deepseek("y", model="deepseek-v4-pro")

        assert result == "y"
        m_record.assert_called_once_with("deepseek", "deepseek-v4-pro", 50, 10)

    # ── AC2: tracking failure does not break the response ──────────

    def test_ac2_tracking_failure_swallowed(self):
        """When record_cost raises, the function still returns the content."""
        fake_data = {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            with patch("core.spend_tracker.spend_tracker.record_cost") as m_record:
                m_record.side_effect = RuntimeError("Backend unavailable")
                result = ask_deepseek("x")

        assert result == "x"
        m_record.assert_called_once()

    # ── AC3: missing usage does not crash ──────────────────────────

    def test_ac3_no_usage_key(self):
        """Response without 'usage' key — no crash, no record_cost call."""
        fake_data = {
            "choices": [{"message": {"content": "x"}}],
            # deliberately no "usage" key
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            with patch("core.spend_tracker.spend_tracker.record_cost") as m_record:
                result = ask_deepseek("x")

        assert result == "x"
        m_record.assert_not_called()

    def test_ac3_usage_all_zero(self):
        """Usage with prompt_tokens=0 and completion_tokens=0 — no record_cost call."""
        fake_data = {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            with patch("core.spend_tracker.spend_tracker.record_cost") as m_record:
                result = ask_deepseek("x")

        assert result == "x"
        m_record.assert_not_called()
# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: disable_thinking parameter
# ═════════════════════════════════════════════════════════════════════════════

class TestDisableThinking:

    def test_call_deepseek_thinking_disabled_top_level(self):
        """disable_thinking=True -> json kwarg includes top-level 'thinking' key."""
        fake_data = {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            result = ask_deepseek("x", disable_thinking=True)

        assert result == "x"
        call_kwargs = m_post.call_args[1]
        sent_json = call_kwargs.get("json", {})
        assert sent_json.get("thinking") == {"type": "disabled"}, (
            f"Expected top-level 'thinking' key, got {sent_json.get('thinking')!r}"
        )
        # Must NOT be under extra_body (SDK flattening pattern — not used here)
        assert "extra_body" not in sent_json, (
            "Should not use extra_body wrapper when building raw HTTP JSON"
        )

    def test_call_deepseek_thinking_default_no_thinking_key(self):
        """disable_thinking=False (default) -> no 'thinking' key in the request json."""
        fake_data = {
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        with patch("llm_api.deepseek_api.deepseek_session.request") as m_post:
            m_post.return_value = _mock_response(fake_data)
            result = ask_deepseek("x")

        assert result == "x"
        call_kwargs = m_post.call_args[1]
        sent_json = call_kwargs.get("json", {})
        assert "thinking" not in sent_json, (
            f"Expected no 'thinking' key with default disable_thinking, "
            f"got 'thinking': {sent_json.get('thinking')!r}"
        )

    def test_classify_and_normalize_passes_disable_thinking(self):
        """classify_and_normalize calls ask_deepseek with disable_thinking=True."""
        from local_tools.citation_search import classify_and_normalize
        # Patch at the import site (citation_search holds its own reference)
        with patch("local_tools.citation_search.ask_deepseek", return_value='{"type": "case_name", "normalized": "test", "original": "test"}') as m_ask:
            result = classify_and_normalize("test query")

        assert result["type"] == "case_name"
        _, kwargs = m_ask.call_args
        assert kwargs.get("disable_thinking") is True, (
            f"Expected disable_thinking=True, got {kwargs.get('disable_thinking')!r}"
        )

    def test_infer_jurisdiction_passes_disable_thinking(self):
        """_infer_jurisdiction_canlii calls ask_deepseek with disable_thinking=True."""
        from local_tools.citation_search import _infer_jurisdiction_canlii
        with patch("local_tools.citation_search.ask_deepseek", return_value='{"jurisdiction": "on"}') as m_ask:
            result = _infer_jurisdiction_canlii("Ontario Family Law Act")

        assert result == "on"
        _, kwargs = m_ask.call_args
        assert kwargs.get("disable_thinking") is True, (
            f"Expected disable_thinking=True, got {kwargs.get('disable_thinking')!r}"
        )
