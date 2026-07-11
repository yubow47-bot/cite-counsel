"""Regression tests for the verified gate on concept routes and citation_select.

Three gaps were fixed:
  A. _handle_concept() single-result branch: unverified → scaffold, not format_citation().
  B. _handle_concept() multi-candidate branch: filter candidates to verified only.
  C. citation_select() endpoint: refuse to format unverified candidates.

Run:  pytest tests/test_verified_gate.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from fastapi.testclient import TestClient
from api.main import (
    app,
    _handle_concept,
    _SCAFFOLD_DISABLED_MSG,
)
from api.scaffold import SUGGESTED_TYPE_MAP, build_prefill

client = TestClient(app)

# ══════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════

_UNVERIFIED_SINGLE = [
    {
        "verified": False,
        "role": "legislation",
        "statute_title": "Unknown Act",
        "citation": "SC 2023, c 1",
    }
]

_VERIFIED_SINGLE = [
    {
        "verified": True,
        "role": "legislation",
        "statute_title": "Criminal Code",
        "citation": "RSC 1985, c C-46",
    }
]

_MIXED_THREE = [
    {
        "verified": False,
        "role": "legislation",
        "statute_title": "Unknown Act",
        "citation": "SC 2023, c 1",
    },
    {
        "verified": True,
        "role": "case",
        "style_of_cause": "R v Known",
        "neutral_citation": "2025 SCC 1",
    },
    {
        "verified": True,
        "role": "case",
        "style_of_cause": "R v Settled",
        "neutral_citation": "2025 SCC 2",
    },
]

_ALL_UNVERIFIED = [
    {
        "verified": False,
        "role": "legislation",
        "statute_title": "Act A",
    },
    {
        "verified": False,
        "role": "legislation",
        "statute_title": "Act B",
    },
]

_MIXED_TWO = [
    {
        "verified": False,
        "role": "legislation",
        "statute_title": "Unknown Act",
    },
    {
        "verified": True,
        "role": "case",
        "style_of_cause": "R v Verified",
        "neutral_citation": "2025 SCC 3",
    },
]

_FORMATTED_CIT = "Criminal Code, RSC 1985, c C-46."
_FORMATTED_CASE = "R v Verified, 2025 SCC 3."


# ══════════════════════════════════════════════════════════════════════
#  Tests 1-5: _handle_concept
# ══════════════════════════════════════════════════════════════════════

def test_1_unverified_single_returns_scaffold():
    """_handle_concept([unverified_single]) → unsupported scaffold, no format_citation."""
    with patch("api.main.format_citation") as mock_fmt:
        result = _handle_concept(_UNVERIFIED_SINGLE, query="Fundamental freedoms")

    # format_citation must NOT be called
    mock_fmt.assert_not_called()

    assert result["status"] in ("unsupported", "needs_input"), (
        f"Expected unsupported or needs_input, got {result['status']!r}"
    )
    assert result["error"]["reason"] == _SCAFFOLD_DISABLED_MSG, (
        f"Expected disabled message, got {result['error']['reason']!r}"
    )


def test_2_verified_single_returns_done():
    """_handle_concept([verified_single]) → status done, citation returned."""
    with patch("api.main.format_citation", return_value=_FORMATTED_CIT):
        result = _handle_concept(_VERIFIED_SINGLE, query="Criminal Code")

    assert result["status"] == "done", (
        f"Expected done, got {result['status']!r}"
    )
    citations = result.get("data", {}).get("citations", [])
    assert len(citations) == 1
    assert citations[0]["citation"] == _FORMATTED_CIT


def test_3_mixed_three_filters_candidates():
    """_handle_concept([unverified, verified, verified]) → only 2 verified in needs_selection."""
    with patch("api.main.format_citation") as mock_fmt:
        result = _handle_concept(_MIXED_THREE, query="Extra-contractual liability")

    # Must NOT format anything (multi-candidate path)
    mock_fmt.assert_not_called()

    assert result["status"] == "needs_selection", (
        f"Expected needs_selection, got {result['status']!r}"
    )
    candidates = result.get("data", {}).get("candidates", [])
    assert len(candidates) == 2, (
        f"Expected 2 candidates, got {len(candidates)}"
    )
    # Both should be the verified items (R v Known, R v Settled)
    for c in candidates:
        assert c.get("verified") is True, (
            f"Candidate must be verified, got {c.get('verified')!r}"
        )
    # Neither should contain the unverified item's name
    names = [c.get("style_of_cause", "") for c in candidates]
    assert "Unknown Act" not in str(candidates)


def test_4_all_unverified_returns_scaffold():
    """_handle_concept([all unverified]) → same scaffold shape as empty results."""
    with patch("api.main.format_citation") as mock_fmt:
        result = _handle_concept(_ALL_UNVERIFIED, query="Some Act")

    mock_fmt.assert_not_called()
    assert result["status"] in ("unsupported", "needs_input"), (
        f"Expected unsupported or needs_input, got {result['status']!r}"
    )
    assert result["error"]["reason"] == _SCAFFOLD_DISABLED_MSG


def test_5_unverified_verified_pair_formats_directly():
    """_handle_concept([unverified, verified]) → 1 verified after filtering → done."""
    with patch("api.main.format_citation", return_value=_FORMATTED_CASE):
        result = _handle_concept(_MIXED_TWO, query="R v Verified")

    assert result["status"] == "done", (
        f"Expected done, got {result['status']!r}"
    )
    citations = result.get("data", {}).get("citations", [])
    assert len(citations) == 1
    assert citations[0]["citation"] == _FORMATTED_CASE


# ══════════════════════════════════════════════════════════════════════
#  Tests 6-8: citation_select endpoint
# ══════════════════════════════════════════════════════════════════════

def test_6_select_unverified_returns_unsupported():
    """citation_select with unverified candidate → unsupported, no format_citation."""
    candidate = {
        "verified": False,
        "style_of_cause": "R v Fake",
        "neutral_citation": "2025 SCC 99",
    }

    with patch("api.main.format_citation") as mock_fmt:
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })

    mock_fmt.assert_not_called()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsupported", (
        f"Expected unsupported, got {body['status']!r}"
    )
    assert body["error"]["reason"] == _SCAFFOLD_DISABLED_MSG, (
        f"Expected disabled message, got {body['error']['reason']!r}"
    )


def test_7_select_verified_returns_done():
    """citation_select with verified candidate → unchanged behavior (done)."""
    candidate = {
        "verified": True,
        "style_of_cause": "R v Gladue",
        "neutral_citation": "[1999] 1 SCR 688",
    }
    formatted = "R v Gladue, [1999] 1 SCR 688."

    with patch("api.main.format_citation", return_value=formatted):
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done", (
        f"Expected done, got {body['status']!r}"
    )
    citations = body.get("data", {}).get("citations", [])
    assert len(citations) == 1
    assert citations[0]["citation"] == formatted


def test_8_select_truthy_non_true_returns_unsupported():
    """citation_select with verified='false' (string) → unsupported, no format_citation.
    Proves the `is not True` identity check, not truthiness."""
    for bogus_value in ("false", 1, "yes", "True"):
        candidate = {
            "verified": bogus_value,
            "style_of_cause": "R v Bogus",
            "neutral_citation": "2025 SCC 99",
        }

        with patch("api.main.format_citation") as mock_fmt:
            resp = client.post("/api/citation/select", json={
                "candidates": [candidate],
                "selected_index": 0,
            })

        mock_fmt.assert_not_called()
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "unsupported", (
            f"verified={bogus_value!r} should give unsupported, got {body['status']!r}"
        )
        assert body["error"]["reason"] == _SCAFFOLD_DISABLED_MSG, (
            f"verified={bogus_value!r} should give disabled message"
        )
