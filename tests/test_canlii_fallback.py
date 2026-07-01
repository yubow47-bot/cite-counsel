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
    assert r["_match_path"] == "exact", (
        f"Expected exact match path, got {r['_match_path']!r}"
    )
    assert r["statute_title"] == "Alberta Human Rights Act"
    assert r["chapter"] == "c A-25.5"
    assert r["jurisdiction"] == "AB"
    assert r["warning"] == ""
    mock_canlii.assert_called_once()
    mock_infer.assert_not_called()  # prescan resolves "alberta" -> "ab", no LLM call


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
         patch("local_tools.utils.a2aj_session.get", mock_get):
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


# ═════════════════════════════════════════════════════════════════════════════
# Test 7: fuzzy match returns multiple candidates (Direction A)
# ═════════════════════════════════════════════════════════════════════════════

CANLII_RESIDUAL_A = {
    "databaseId": "ons", "legislationId": "so-ns",
    "title": "Nova Scotia",
    "citation": "SO 2000, c 1", "type": "STATUTE",
}
CANLII_RESIDUAL_B = {
    "databaseId": "ons", "legislationId": "so-ca",
    "title": "Canada",
    "citation": "SO 2000, c 2", "type": "STATUTE",
}


def test_canlii_fallback_fuzzy_multiple():
    """Direction A with >=2 survivors -> returns list for needs_selection."""
    mock_canlii = MagicMock(return_value={
        "legislations": [CANLII_RESIDUAL_A, CANLII_RESIDUAL_B]
    })
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Nova Scotia Canada",
        "original": "Nova Scotia Canada",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Nova Scotia Canada", classification)

    assert len(result) == 2
    for r in result:
        assert r["verified"] is True
        assert r["source"] == "canlii"
        assert r["jurisdiction"] == "ON"
    assert result[0]["chapter"] == "c 1"
    assert result[1]["chapter"] == "c 2"


# ═════════════════════════════════════════════════════════════════════════════
# Test 8: Direction A single match (province-prefixed query, generic candidate)
# ═════════════════════════════════════════════════════════════════════════════

RECORD_HIGHWAY_TRAFFIC = {
    "databaseId": "ons", "legislationId": "so-hta",
    "title": "Highway Traffic Act",
    "citation": "RSO 1990, c H.8", "type": "STATUTE",
}

RECORD_THE_MARRIAGE = {
    "databaseId": "cas", "legislationId": "federal-marriage",
    "title": "The Marriage (Prohibited Degrees) Act",
    "citation": "SC 1990, c 46", "type": "STATUTE",
}


def test_canlii_fallback_fuzzy_single():
    """Direction A match (province in query, generic candidate) -> verified=True."""
    mock_canlii = MagicMock(return_value={
        "legislations": [RECORD_HIGHWAY_TRAFFIC]
    })
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Ontario Highway Traffic Act",
        "original": "Ontario Highway Traffic Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Ontario Highway Traffic Act", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["_match_path"] == "direction_a_residual_ok"
    assert r["statute_title"] == "Highway Traffic Act"
    assert r["chapter"] == "c H.8"
    assert r["jurisdiction"] == "ON"


# ═════════════════════════════════════════════════════════════════════════════
# Test 9: Direction A cap — many candidates capped at 10
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_fallback_fuzzy_cap():
    """Direction A with many matching substrings -> capped at 10."""
    many = []
    for i in range(20):
        rec = dict(RECORD_HIGHWAY_TRAFFIC)
        # Each candidate is a valid substring of "Ontario Canada Highway Traffic Act"
        rec["title"] = f"Highway Traffic Act"  # constant — same substring for all 20
        rec["citation"] = f"SO 2000, c {i}"
        rec["legislationId"] = f"so-2000-c-{i}"
        many.append(rec)

    mock_canlii = MagicMock(return_value={"legislations": many})
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Ontario Canada Highway Traffic Act",
        "original": "Ontario Canada Highway Traffic Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Ontario Canada Highway Traffic Act", classification)

    # capped at 10 (all 20 match but result list is limited)
    assert len(result) == 10


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: leading-article normalization (exact, not fuzzy)
# ═════════════════════════════════════════════════════════════════════════════

def test_leading_article_normalization():
    """'Marriage (Prohibited Degrees) Act' with 'The Marriage...' -> exact match, _match_path=exact."""
    rec = {
        "databaseId": "cas", "legislationId": "federal-marriage",
        "title": "The Marriage (Prohibited Degrees) Act",
        "citation": "SC 1990, c 46", "type": "STATUTE",
    }
    mock_canlii = MagicMock(return_value={"legislations": [rec]})
    mock_infer = MagicMock(return_value="ca")

    classification = {
        "type": "legislation",
        "normalized": "Marriage (Prohibited Degrees) Act",
        "original": "Marriage (Prohibited Degrees) Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer),          patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Marriage (Prohibited Degrees) Act", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["_match_path"] == "exact", (
        f"Expected exact match via article normalization, got {r['_match_path']!r}"
    )
    assert r["statute_title"] == "The Marriage (Prohibited Degrees) Act"



# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: Direction A province-prefix (positive)
# ═════════════════════════════════════════════════════════════════════════════

RECORD_FAMILY_LAW = {
    "databaseId": "bcs", "legislationId": "bc-family",
    "title": "Family Law Act",
    "citation": "SBC 2011, c 25", "type": "STATUTE",
}


def test_direction_a_province_prefix():
    """British Columbia Family Law Act + Family Law Act -> Direction A, residual OK."""
    mock_canlii = MagicMock(return_value={"legislations": [RECORD_FAMILY_LAW]})
    mock_infer = MagicMock(return_value="bc")

    classification = {
        "type": "legislation",
        "normalized": "British Columbia Family Law Act",
        "original": "British Columbia Family Law Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer),          patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("British Columbia Family Law Act", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["_match_path"] == "direction_a_residual_ok"
    assert r["statute_title"] == "Family Law Act"
    assert r["jurisdiction"] == "BC"


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: Direction B removed — amending act rejection (modify variant)
# ═════════════════════════════════════════════════════════════════════════════

RECORD_AMENDING_MODIFY = {
    "databaseId": "cas", "legislationId": "fed-modify",
    "title": "An Act to modify the Constitution Act, 1867",
    "citation": "SC 2024, c 1", "type": "STATUTE",
}


def test_direction_b_removed_modify_act():
    """Direction B removed: modify act rejected when query is shorter (no blacklist)."""
    mock_canlii = MagicMock(return_value={"legislations": [RECORD_AMENDING_MODIFY]})
    mock_infer = MagicMock(return_value="ca")

    classification = {
        "type": "legislation",
        "normalized": "Constitution Act, 1867",
        "original": "Constitution Act, 1867",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer),          patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Constitution Act, 1867", classification)

    # The constitutional guard intercepts this in production.
    assert len(result) == 1
    r = result[0]
    assert r["statute_title"] == "Constitution Act, 1867"
    assert r["verified"] is True


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: Direction A no-match (Traffic Offences -> Traffic Act)
# ═════════════════════════════════════════════════════════════════════════════

RECORD_TRAFFIC_ACT = {
    "databaseId": "ons", "legislationId": "so-traffic",
    "title": "Traffic Act",
    "citation": "RSO 1990, c T.1", "type": "STATUTE",
}


def test_direction_a_no_match():
    """Traffic Offences Act 2020 + Traffic Act -> NOT verified (no direction match)."""
    mock_canlii = MagicMock(return_value={"legislations": [RECORD_TRAFFIC_ACT]})
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Traffic Offences Act 2020",
        "original": "Traffic Offences Act 2020",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer),          patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Traffic Offences Act 2020", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert "⚠️" in r["warning"]


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: Quebec long-title is intentionally degraded (false-neg acknowledgment)
# ═════════════════════════════════════════════════════════════════════════════

RECORD_QC_LONG = {
    "databaseId": "qcs", "legislationId": "qc-access",
    "title": "Act respecting Access to documents held by public bodies and the Protection of personal information",
    "citation": "RLRQ, c A-2.1", "type": "STATUTE",
}


def test_quebec_long_title_safe_degrade():
    """Quebec long-title -> NOT verified.  Intentional safe degrade (broader act)."""
    mock_canlii = MagicMock(return_value={"legislations": [RECORD_QC_LONG]})
    mock_infer = MagicMock(return_value="qc")

    classification = {
        "type": "legislation",
        "normalized": "Act respecting Access to documents held by public bodies",
        "original": "Act respecting Access to documents held by public bodies",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer),          patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation(
            "Act respecting Access to documents held by public bodies",
            classification,
        )

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert "⚠️" in r["warning"]

# ═════════════════════════════════════════════════════════════════════════════
#  CHANGE 1: deterministic jurisdiction prescan
# ═════════════════════════════════════════════════════════════════════════════

RECORD_ANIMAL_PROTECTION = {
    "databaseId": "abs", "legislationId": "rsa-2000-c-a-30",
    "title": "Animal Protection Act",
    "citation": "RSA 2000, c A-30", "type": "STATUTE",
}

RECORD_SOME_ACT = {
    "databaseId": "ons", "legislationId": "so-2000-c-1",
    "title": "Some Act",
    "citation": "SO 2000, c 1", "type": "STATUTE",
}


def test_prescan_resolves_jurisdiction_before_llm():
    """Query has 'alberta' -> prescan yields ab, LLM never called, verified=True."""
    mock_canlii = MagicMock(return_value=_canlii_resp(RECORD_ANIMAL_PROTECTION))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    classification = {
        "type": "legislation",
        "normalized": "Animal Protection Act",
        "original": "animal protection act alberta",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("animal protection act alberta", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["_match_path"] == "exact"
    assert r["jurisdiction"] == "AB"
    mock_infer.assert_not_called()
    mock_canlii.assert_called_once()


def test_prescan_no_province_delegates_to_llm():
    """Query has no province token -> prescan returns None, LLM IS called."""
    mock_canlii = MagicMock(return_value=_canlii_resp(RECORD_SOME_ACT))
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Some Act",
        "original": "Some Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Some Act", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    mock_infer.assert_called_once()
    mock_canlii.assert_called_once()


def test_prescan_two_provinces_defers_to_llm():
    """Query has two distinct province tokens -> prescan defers to LLM."""
    mock_canlii = MagicMock()
    mock_infer = MagicMock(return_value="on")

    # "nova scotia" -> ns, "canada" -> ca  => 2 distinct codes => prescan returns None
    classification = {
        "type": "legislation",
        "normalized": "Nova Scotia Canada",
        "original": "Nova Scotia Canada",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        search_citation("Nova Scotia Canada", classification)

    mock_infer.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
#  CHANGE 2: observability — _match_path values on failure paths
# ═════════════════════════════════════════════════════════════════════════════

def test_canlii_error_sets_match_path():
    """browse_legislation_in_database returns error -> _match_path=canlii_error."""
    mock_canlii = MagicMock(return_value={"error": "CanLII request failed: 500"})
    mock_infer = MagicMock(return_value="ab")

    classification = {
        "type": "legislation",
        "normalized": "Alberta Human Rights Act",
        "original": "Alberta Human Rights Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Alberta Human Rights Act", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is False
    assert r["_match_path"] == "canlii_error"
    assert "failed through A2AJ" in r["warning"] or "建议在 CanLII 手动确认" in r["warning"]
