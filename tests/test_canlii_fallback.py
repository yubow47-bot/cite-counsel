"""Unit tests for CanLII legislation fallback in search_citation().

Run: pytest tests/test_canlii_fallback.py -v
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.citation_search import search_citation, _normalize_for_match

# ── Realistic CanLII record matching Step 1 verified field structure ──

CANLII_RECORD_AB = {
    "databaseId": "abs",
    "legislationId": "rsa-2000-c-a-25.5",
    "longUrl": "https://www.canlii.org/en/ab/laws/stat/rsa-2000-c-a-25.5/latest/rsa-2000-c-a-25.5.html",
    "title": "Alberta Human Rights Act",
    "citation": "RSA 2000, c A-25.5",
    "type": "STATUTE",
    "aiContentId": "rsa-2000-c-a-25.5",
}

CANLII_RECORD_OTHER = {
    "databaseId": "abs",
    "legislationId": "rsa-2000-c-something",
    "title": "Some Other Act",
    "citation": "RSA 2000, c S-1",
    "type": "STATUTE",
}

# ── Pre-computed classifications (avoid LLM calls in tests) ──

CLASSIFICATION_AB = {
    "type": "legislation",
    "normalized": "Alberta Human Rights Act",
    "original": "Alberta Human Rights Act",
}

CLASSIFICATION_CRIMINAL_CODE = {
    "type": "legislation",
    "normalized": "Criminal Code, RSC 1985, c C-46",
    "original": "Criminal Code, RSC 1985, c C-46",
}

# ── Helper ──

def _canlii_resp(*records):
    """Build a CanLII response dict matching Step 1 verified top-level key."""
    return {"legislations": list(records)}


# ═════════════════════════════════════════════════════════════════════════════
# Test 1: exact match hit
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_fallback_exact_match_hit():
    """Mock CanLII returns one record whose title normalizes to exact match."""
    mock_canlii = MagicMock(return_value=_canlii_resp(CANLII_RECORD_AB))
    mock_infer = MagicMock(return_value="ab")

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Alberta Human Rights Act", CLASSIFICATION_AB)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["statute_title"] == "Alberta Human Rights Act"
    assert r["chapter"] == "c A-25.5"
    assert r["jurisdiction"] == "AB"
    assert r["warning"] == ""
    mock_canlii.assert_called_once()
    mock_infer.assert_called_once_with("Alberta Human Rights Act")


# ═════════════════════════════════════════════════════════════════════════════
# Test 2: no match
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_fallback_no_match():
    """CanLII returns records, none normalize to match -> verified=False."""
    mock_canlii = MagicMock(
        return_value=_canlii_resp(CANLII_RECORD_OTHER, CANLII_RECORD_AB)
    )
    mock_infer = MagicMock(return_value="ab")

    classification = {
        "type": "legislation",
        "normalized": "Employment Standards Code",
        "original": "Employment Standards Code",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Employment Standards Code", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert "建议在 CanLII 手动确认" in r["warning"]
    mock_canlii.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# Test 3: multiple match -> don't guess
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_fallback_multiple_match():
    """CanLII returns 2 records both normalizing to same -> verified=False (no guessing)."""
    dup_a = dict(CANLII_RECORD_AB)
    dup_b = dict(CANLII_RECORD_AB)
    dup_b["legislationId"] = "different-id"

    mock_canlii = MagicMock(return_value=_canlii_resp(dup_a, dup_b))
    mock_infer = MagicMock(return_value="ab")

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Alberta Human Rights Act", CLASSIFICATION_AB)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert "建议在 CanLII 手动确认" in r["warning"]


# ═════════════════════════════════════════════════════════════════════════════
# Test 4: API error -> graceful fall through
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_fallback_api_error():
    """browse_legislation_in_database returns {"error": ...} -> verified=False, no crash."""
    mock_canlii = MagicMock(return_value={"error": "CanLII request failed: 500"})
    mock_infer = MagicMock(return_value="on")

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Employment Standards Act", CLASSIFICATION_AB)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert "建议在 CanLII 手动确认" in r["warning"]


# ═════════════════════════════════════════════════════════════════════════════
# Test 5: jurisdiction unknown -> don't trigger CanLII
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_fallback_jurisdiction_unknown():
    """_infer_jurisdiction_canlii returns None -> skip CanLII, return warning."""
    mock_infer = MagicMock(return_value=None)
    mock_canlii = MagicMock()

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Some Unknown Act", CLASSIFICATION_AB)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert "建议在 CanLII 手动确认" in r["warning"]
    mock_infer.assert_called_once()
    mock_canlii.assert_not_called()  # CanLII never triggered


# ═════════════════════════════════════════════════════════════════════════════
# Test 6: cit_match hit path unchanged — A2AJ called, CanLII NOT called
# ═════════════════════════════════════════════════════════════════════════════

def test_cit_match_hit_unchanged():
    """cit_match hit: A2AJ path runs; CanLII functions never called (regression guard)."""
    mock_infer = MagicMock()
    mock_canlii = MagicMock()

    # Mock A2AJ /fetch response
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [{
            "name_en": "Criminal Code",
            "citation_en": "RSC 1985, c C-46",
            "dataset": "FED",
        }]
    }
    mock_get = MagicMock(return_value=mock_resp)

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii), \
         patch("requests.get", mock_get):
        result = search_citation(
            "Criminal Code, RSC 1985, c C-46",
            CLASSIFICATION_CRIMINAL_CODE,
        )

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["statute_title"] == "Criminal Code"
    assert r["chapter"] == "c C-46"
    assert r["jurisdiction"] == "Canada"

    # Regression guard: CanLII path NEVER activated
    mock_infer.assert_not_called()
    mock_canlii.assert_not_called()
