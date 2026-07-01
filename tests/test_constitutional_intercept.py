"""Unit tests for Phase 2 constitutional statute intercept in search_citation().

Verifies that the pre-routing guard in the legislation branch intercepts
closed-set constitutional titles BEFORE the CanLII fallback (or A2AJ) chain,
returning the canonical title with verified=True and correct pinpoint.

Each test structurally asserts call_count == 0 for both the A2AJ endpoint
(requests.get) and the CanLII fallback entry points (_infer_jurisdiction_canlii,
browse_legislation_in_database), proving the external API chain is never
reached for constitutional inputs.

Run:  pytest tests/test_constitutional_intercept.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.citation_search import search_citation


# ── Pre-computed classifications (avoid LLM calls) ──

CLASSIFICATION_1867 = {
    "type": "legislation",
    "normalized": "Constitution Act, 1867",
    "original": "Constitution Act, 1867",
}

CLASSIFICATION_1867_S91 = {
    "type": "legislation",
    "normalized": "Constitution Act, 1867, s 91",
    "original": "Constitution Act, 1867, s 91",
}

CLASSIFICATION_CHARTER_S7 = {
    "type": "legislation",
    "normalized": "Canadian Charter of Rights and Freedoms, s 7",
    "original": "Canadian Charter of Rights and Freedoms, s 7",
}

CLASSIFICATION_1982_S35 = {
    "type": "legislation",
    "normalized": "Constitution Act, 1982, s 35",
    "original": "Constitution Act, 1982, s 35",
}


# ═══════════════════════════════════════════════════════════════════════
# Tests — structural proof that external API chain is NEVER invoked
# ═══════════════════════════════════════════════════════════════════════


def test_constitutional_1867_no_pinpoint():
    """'Constitution Act, 1867' — A2AJ + CanLII call_count == 0."""
    mock_a2aj = MagicMock()
    mock_infer = MagicMock()
    mock_canlii = MagicMock()

    with patch("local_tools.utils.a2aj_session.get", mock_a2aj), \
         patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Constitution Act, 1867", CLASSIFICATION_1867)

    # Structural: no external API was called
    assert mock_a2aj.call_count == 0, f"A2AJ requests.get was called {mock_a2aj.call_count} times"
    assert mock_infer.call_count == 0, f"_infer_jurisdiction_canlii was called {mock_infer.call_count} times"
    assert mock_canlii.call_count == 0, f"browse_legislation_in_database was called {mock_canlii.call_count} times"

    # Output correctness (secondary — the structural proof above is the guard check)
    assert len(result) == 1
    r = result[0]
    assert r["statute_title"] == "Constitution Act, 1867", (
        f"Expected 'Constitution Act, 1867', got {r['statute_title']!r}"
    )
    assert r["verified"] is True
    assert r["pinpoint"] is None


def test_constitutional_1867_with_pinpoint():
    """'Constitution Act, 1867, s 91' — A2AJ + CanLII call_count == 0."""
    mock_a2aj = MagicMock()
    mock_infer = MagicMock()
    mock_canlii = MagicMock()

    with patch("local_tools.utils.a2aj_session.get", mock_a2aj), \
         patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Constitution Act, 1867, s 91", CLASSIFICATION_1867_S91)

    # Structural: no external API was called
    assert mock_a2aj.call_count == 0, f"A2AJ requests.get was called {mock_a2aj.call_count} times"
    assert mock_infer.call_count == 0, f"_infer_jurisdiction_canlii was called {mock_infer.call_count} times"
    assert mock_canlii.call_count == 0, f"browse_legislation_in_database was called {mock_canlii.call_count} times"

    # Output correctness (secondary)
    assert len(result) == 1
    r = result[0]
    assert r["statute_title"] == "Constitution Act, 1867", (
        f"Expected 'Constitution Act, 1867', got {r['statute_title']!r}"
    )
    assert r["verified"] is True
    assert r["pinpoint"] == "s 91", f"Expected 's 91', got {r['pinpoint']!r}"


def test_constitutional_charter_with_pinpoint():
    """'Canadian Charter of Rights and Freedoms, s 7' — A2AJ + CanLII call_count == 0."""
    mock_a2aj = MagicMock()
    mock_infer = MagicMock()
    mock_canlii = MagicMock()

    with patch("local_tools.utils.a2aj_session.get", mock_a2aj), \
         patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation(
            "Canadian Charter of Rights and Freedoms, s 7",
            CLASSIFICATION_CHARTER_S7,
        )

    assert mock_a2aj.call_count == 0
    assert mock_infer.call_count == 0
    assert mock_canlii.call_count == 0

    assert len(result) == 1
    r = result[0]
    assert r["statute_title"] == "Canadian Charter of Rights and Freedoms", (
        f"Expected 'Canadian Charter of Rights and Freedoms', got {r['statute_title']!r}"
    )
    assert r["verified"] is True
    assert r["pinpoint"] == "s 7"


def test_constitutional_1982_with_pinpoint():
    """'Constitution Act, 1982, s 35' — A2AJ + CanLII call_count == 0."""
    mock_a2aj = MagicMock()
    mock_infer = MagicMock()
    mock_canlii = MagicMock()

    with patch("local_tools.utils.a2aj_session.get", mock_a2aj), \
         patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Constitution Act, 1982, s 35", CLASSIFICATION_1982_S35)

    assert mock_a2aj.call_count == 0
    assert mock_infer.call_count == 0
    assert mock_canlii.call_count == 0

    assert len(result) == 1
    r = result[0]
    assert r["statute_title"] == "Constitution Act, 1982", (
        f"Expected 'Constitution Act, 1982', got {r['statute_title']!r}"
    )
    assert r["verified"] is True
    assert r["pinpoint"] == "s 35"


def test_constitutional_canada_act_intercept():
    """'Canada Act 1982' — A2AJ + CanLII call_count == 0."""
    classification = {
        "type": "legislation",
        "normalized": "Canada Act 1982",
        "original": "Canada Act 1982",
    }
    mock_a2aj = MagicMock()
    mock_infer = MagicMock()
    mock_canlii = MagicMock()

    with patch("local_tools.utils.a2aj_session.get", mock_a2aj), \
         patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Canada Act 1982", classification)

    assert mock_a2aj.call_count == 0
    assert mock_infer.call_count == 0
    assert mock_canlii.call_count == 0

    assert len(result) == 1
    r = result[0]
    assert r["statute_title"] == "Canada Act 1982", (
        f"Expected 'Canada Act 1982', got {r['statute_title']!r}"
    )
    assert r["verified"] is True
