"""Unit tests for classify_and_normalize() in citation_search.py.

Run: pytest tests/test_classify_normalize.py -v
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.citation_search import classify_and_normalize


# ═════════════════════════════════════════════════════════════════════════════
# Regression: Bug 2a — content unbound when ask_deepseek raises
# ═════════════════════════════════════════════════════════════════════════════

def test_classify_and_normalize_deepseek_raises_connection_error():
    """ask_deepseek raises ConnectionError -> fallback dict, no UnboundLocalError."""
    with patch("local_tools.citation_search.ask_deepseek", side_effect=ConnectionError("Connection refused")), \
         patch("profiling.timing.ENABLED", False):
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


def test_classify_and_normalize_deepseek_raises_timeout():
    """ask_deepseek raises TimeoutError -> fallback dict, no UnboundLocalError."""
    with patch("local_tools.citation_search.ask_deepseek", side_effect=TimeoutError("timed out")), \
         patch("profiling.timing.ENABLED", False):
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


def test_classify_and_normalize_deepseek_raises_generic_exception():
    """ask_deepseek raises generic Exception -> fallback dict, no UnboundLocalError."""
    with patch("local_tools.citation_search.ask_deepseek", side_effect=RuntimeError("API failure")), \
         patch("profiling.timing.ENABLED", False):
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


# ═════════════════════════════════════════════════════════════════════════════
# Happy path: bill fast-path (no LLM call needed)
# ═════════════════════════════════════════════════════════════════════════════

def test_classify_and_normalize_bill_fastpath():
    """Bill prefix triggers regex fast-path, no LLM call."""
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = classify_and_normalize("Bill C-22")

    assert result["type"] == "bill"
    assert result["normalized"] == "C-22"
    mock_ds.assert_not_called()
