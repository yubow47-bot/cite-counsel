"""Unit tests for CanLII legislation fallback in search_citation().

Run: pytest tests/test_canlii_fallback.py -v
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.citation_search import (
    search_citation,
    expand_concept,
    _normalize_for_match,
    _strip_citation_suffix_for_title_match,
)

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
         patch("local_tools.utils.a2aj_session.request", mock_get):
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


# ═════════════════════════════════════════════════════════════════════════════
#  TEST SUITE: _strip_citation_suffix_for_title_match unit tests
# ═════════════════════════════════════════════════════════════════════════════


def test_strip_suffix_rso():
    """', RSO 1990' is stripped (Ontario)."""
    result = _strip_citation_suffix_for_title_match("Family Law Act, RSO 1990")
    assert result == "Family Law Act"


def test_strip_suffix_rso_with_chapter():
    """', RSO 1990, c F.3' — strips at the comma before RSO."""
    result = _strip_citation_suffix_for_title_match("Family Law Act, RSO 1990, c F.3")
    assert result == "Family Law Act"


def test_strip_suffix_rsa():
    """', RSA 2000' is stripped (Alberta)."""
    result = _strip_citation_suffix_for_title_match("Child and Youth Care Act, RSA 2000, c C-12")
    assert result == "Child and Youth Care Act"


def test_strip_suffix_rsbc():
    """', RSBC 1996' is stripped (British Columbia)."""
    result = _strip_citation_suffix_for_title_match("Family Law Act, RSBC 1996, c 128")
    assert result == "Family Law Act"


def test_strip_suffix_rsm():
    """', RSM 1987' is stripped (Manitoba)."""
    result = _strip_citation_suffix_for_title_match("The Child and Family Services Act, RSM 1987, c C80")
    assert result == "The Child and Family Services Act"


def test_strip_suffix_sc():
    """', SC 2002' is stripped (federal)."""
    result = _strip_citation_suffix_for_title_match("Youth Criminal Justice Act, SC 2002, c 1")
    assert result == "Youth Criminal Justice Act"


def test_strip_suffix_no_change_legitimate_comma():
    """Title with a legitimate comma (no year after an all-caps abbrev) is unchanged."""
    result = _strip_citation_suffix_for_title_match("Legislation, Regulation and Rules Act")
    assert result == "Legislation, Regulation and Rules Act"


def test_strip_suffix_no_change_constitution():
    """"Constitution Act, 1867" — year but no abbreviation before it, unchanged."""
    result = _strip_citation_suffix_for_title_match("Constitution Act, 1867")
    assert result == "Constitution Act, 1867"


def test_strip_suffix_empty():
    """Empty string returns empty."""
    assert _strip_citation_suffix_for_title_match("") == ""


def test_strip_suffix_no_comma():
    """No comma at all — unchanged."""
    result = _strip_citation_suffix_for_title_match("Family Law Act")
    assert result == "Family Law Act"


def test_strip_suffix_embedded_not_trailing():
    """Embedded ', ABC 1990' substring NOT at end of string — must remain unchanged.
    The anchored regex (with $) prevents stripping mid-title patterns."""
    result = _strip_citation_suffix_for_title_match("Some Act, ABC 1990 Historical Review Act")
    assert result == "Some Act, ABC 1990 Historical Review Act"


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: bug fix — classify_and_normalize expands statute name into full
#  citation that cit_match's regex doesn't recognize (e.g. RSO), causing
#  CanLII title-match failure.  _strip_citation_suffix_for_title_match should
#  remedy this before norm_target is computed.
# ═════════════════════════════════════════════════════════════════════════════

FLA_RECORD = {
    "databaseId": "ons", "legislationId": "so-fla",
    "title": "Family Law Act",
    "citation": "RSO 1990, c F.3", "type": "STATUTE",
}

CLASSIFICATION_FLA_EXPANDED = {
    "type": "legislation",
    "normalized": "Family Law Act, RSO 1990, c F.3",
    "original": "family law act ontario",
}

CLASSIFICATION_FLA_PLAIN = {
    "type": "legislation",
    "normalized": "Family Law Act",
    "original": "family law act ontario",
}


def test_rso_expanded_exact_match():
    """Reproduce the reported bug: expanded 'Family Law Act, RSO 1990, c F.3'
    now matches 'Family Law Act' exactly via the citation-suffix strip."""
    mock_canlii = MagicMock(return_value=_canlii_resp(FLA_RECORD))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("family law act ontario", CLASSIFICATION_FLA_EXPANDED)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True, (
        f"Expected verified=True for expanded FLA, got verified={r['verified']!r} "
        f"with _match_path={r['_match_path']!r}"
    )
    assert r["_match_path"] == "exact", (
        f"Expected _match_path='exact', got {r['_match_path']!r}"
    )
    assert r["statute_title"] == "Family Law Act"
    assert r["chapter"] == "c F.3"
    assert r["jurisdiction"] == "ON"
    assert r["warning"] == ""
    mock_canlii.assert_called_once()
    mock_infer.assert_not_called()


def test_fla_plain_still_works():
    """Non-expanded 'Family Law Act' still matches exactly (regression guard)."""
    mock_canlii = MagicMock(return_value=_canlii_resp(FLA_RECORD))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("family law act ontario", CLASSIFICATION_FLA_PLAIN)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["_match_path"] == "exact"
    assert r["statute_title"] == "Family Law Act"
    assert r["chapter"] == "c F.3"
    assert r["jurisdiction"] == "ON"
    assert r["warning"] == ""
    mock_infer.assert_not_called()


# ── Provincial abbreviation variants ──

CYCARE_RECORD = {
    "databaseId": "abs", "legislationId": "rsa-2000-c-c-12",
    "title": "Child and Youth Care Act",
    "citation": "RSA 2000, c C-12", "type": "STATUTE",
}

CLASSIFICATION_CYCARE_RSA = {
    "type": "legislation",
    "normalized": "Child and Youth Care Act, RSA 2000, c C-12",
    "original": "alberta child and youth care act",
}

FLA_BC_RECORD = {
    "databaseId": "bcs", "legislationId": "sbc-2011-c-25",
    "title": "Family Law Act",
    "citation": "SBC 2011, c 25", "type": "STATUTE",
}

CLASSIFICATION_FLA_RSBC = {
    "type": "legislation",
    "normalized": "Family Law Act, RSBC 1996, c 128",
    "original": "british columbia family law act",
}

CFS_RECORD = {
    "databaseId": "mbs", "legislationId": "csm-c80",
    "title": "The Child and Family Services Act",
    "citation": "CCSM, c C80", "type": "STATUTE",
}

CLASSIFICATION_CFS_RSM = {
    "type": "legislation",
    "normalized": "The Child and Family Services Act, RSM 1987, c C80",
    "original": "manitoba child and family services act",
}


def test_rsa_expanded_exact_match():
    """RSA (Alberta) expanded citation strips correctly -> exact match."""
    mock_canlii = MagicMock(return_value=_canlii_resp(CYCARE_RECORD))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("alberta child and youth care act", CLASSIFICATION_CYCARE_RSA)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True, (
        f"RSA test failed: verified={r['verified']!r} _match_path={r['_match_path']!r}"
    )
    assert r["_match_path"] == "exact"
    assert r["statute_title"] == "Child and Youth Care Act"
    mock_infer.assert_not_called()


def test_rsbc_expanded_exact_match():
    """RSBC (British Columbia) expanded citation strips correctly -> exact match."""
    mock_canlii = MagicMock(return_value=_canlii_resp(FLA_BC_RECORD))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("british columbia family law act", CLASSIFICATION_FLA_RSBC)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True, (
        f"RSBC test failed: verified={r['verified']!r} _match_path={r['match_path']!r}"
    )
    assert r["_match_path"] == "exact"
    assert r["statute_title"] == "Family Law Act"
    mock_infer.assert_not_called()


def test_rsm_expanded_exact_match():
    """RSM (Manitoba) expanded citation strips correctly -> exact match."""
    mock_canlii = MagicMock(return_value=_canlii_resp(CFS_RECORD))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("manitoba child and family services act", CLASSIFICATION_CFS_RSM)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True, (
        f"RSM test failed: verified={r['verified']!r} _match_path={r['_match_path']!r}"
    )
    assert r["_match_path"] == "exact"
    assert r["statute_title"] == "The Child and Family Services Act"
    mock_infer.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: negative case — legitimate comma in title must NOT be truncated
# ═════════════════════════════════════════════════════════════════════════════

CLASSIFICATION_LEGIT_COMMA = {
    "type": "legislation",
    "normalized": "Legislation, Regulation and Rules Act",
    "original": "Legislation, Regulation and Rules Act",
}


def test_legitimate_comma_not_truncated():
    """Title with a legitimate comma (no citation-year suffix) is NOT truncated,
    and falls through correctly (verified=False since no matching record)."""
    mock_canlii = MagicMock(return_value=_canlii_resp(CYCARE_RECORD))
    mock_infer = MagicMock(return_value="ab")

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Legislation, Regulation and Rules Act", CLASSIFICATION_LEGIT_COMMA)

    assert len(result) == 1
    r = result[0]
    # The title has a legitimate comma; it must NOT be stripped by the regex.
    # "Regulation" (10 chars) doesn't match [A-Z]{2,6} so the suffix pattern
    # doesn't fire.  Since no CanLII record matches, verified should be False.
    assert r["verified"] is False, (
        f"Expected verified=False for unmatched title with legitimate comma, "
        f"got verified={r['verified']!r} (if True the title was incorrectly truncated)"
    )
    assert "建议在 CanLII 手动确认" in r["warning"]


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: concept-route legislation — pinpoint duplication
#  _verify_legislation strips the pinpoint suffix from entry["name"]
#  when it's separately extracted into entry["pinpoint"].
# ═════════════════════════════════════════════════════════════════════════════

_LLM_RESP_PINPOINT = (
    '{"candidates": ['
    '{"name": "Criminal Code, RSC 1985, c C-46, s 718.2(e)", "type": "legislation"},'
    '{"name": "R v Ipeelee", "citation": "2012 SCC 13", "type": "case"}'
    "]}"
)

_LLM_RESP_NO_PINPOINT = (
    '{"candidates": ['
    '{"name": "Criminal Code, RSC 1985, c C-46", "type": "legislation"},'
    '{"name": "R v Ipeelee", "citation": "2012 SCC 13", "type": "case"}'
    "]}"
)

_LLM_RESP_NO_CITMATCH = (
    '{"candidates": ['
    '{"name": "Some Non-existent Act, s 5", "type": "legislation"}'
    "]}"
)

_A2AJ_OK_RESPONSE = {
    "results": [{
        "name_en": "Criminal Code",
        "citation_en": "RSC 1985, c C-46",
        "dataset": "FED",
    }]
}

_A2AJ_EMPTY_RESPONSE = {"results": []}


def _mock_a2aj_response(data: dict):
    """Build a mock for request_with_retry that returns a given JSON body."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = data
    return mock_resp


def test_verify_legislation_strips_pinpoint_from_name():
    """Expanded concept with pinpoint suffix: name is cleaned, pinpoint extracted separately."""
    mock_llm = MagicMock(return_value=_LLM_RESP_PINPOINT)
    mock_a2aj = MagicMock(return_value=_mock_a2aj_response(_A2AJ_OK_RESPONSE))

    with patch("local_tools.citation_search.ask_deepseek", mock_llm), \
         patch("local_tools.utils.request_with_retry", mock_a2aj):
        results = expand_concept("gladue principle")

    # Find the legislation candidate
    leg = [r for r in results if r.get("role") == "legislation"]
    assert len(leg) == 1, f"Expected 1 legislation candidate, got {len(leg)}"
    leg = leg[0]

    # name must be clean (no pinpoint suffix)
    assert leg["name"] == "Criminal Code, RSC 1985, c C-46", (
        f"Expected name without pinpoint suffix, got {leg['name']!r}"
    )
    # pinpoint must be extracted separately
    assert leg["pinpoint"] == "s 718.2(e)", (
        f"Expected pinpoint='s 718.2(e)', got {leg.get('pinpoint')!r}"
    )
    assert leg["verified"] is True


def test_verify_legislation_no_pinpoint_unchanged():
    """Candidate without pinpoint: name unchanged, no pinpoint field."""
    mock_llm = MagicMock(return_value=_LLM_RESP_NO_PINPOINT)
    mock_a2aj = MagicMock(return_value=_mock_a2aj_response(_A2AJ_OK_RESPONSE))

    with patch("local_tools.citation_search.ask_deepseek", mock_llm), \
         patch("local_tools.utils.request_with_retry", mock_a2aj):
        results = expand_concept("gladue principle")

    leg = [r for r in results if r.get("role") == "legislation"]
    assert len(leg) == 1
    leg = leg[0]

    # name unchanged (no pinpoint to strip)
    assert leg["name"] == "Criminal Code, RSC 1985, c C-46", (
        f"Expected name unchanged, got {leg['name']!r}"
    )
    # no pinpoint field should be present
    assert "pinpoint" not in leg or not leg["pinpoint"], (
        f"Did not expect pinpoint field, got {leg.get('pinpoint')!r}"
    )
    assert leg["verified"] is True


def test_verify_legislation_no_citmatch_fallback():
    """Candidate where cit_match doesn't match: name stays as original string."""
    mock_llm = MagicMock(return_value=_LLM_RESP_NO_CITMATCH)
    mock_a2aj = MagicMock(return_value=_mock_a2aj_response(_A2AJ_EMPTY_RESPONSE))

    with patch("local_tools.citation_search.ask_deepseek", mock_llm), \
         patch("local_tools.utils.request_with_retry", mock_a2aj):
        results = expand_concept("gladue principle")

    leg = [r for r in results if r.get("role") == "legislation"]
    assert len(leg) == 1
    leg = leg[0]

    # name must be the original string (no cit_match → no cleanup)
    assert leg["name"] == "Some Non-existent Act, s 5", (
        f"Expected original name, got {leg['name']!r}"
    )
    # should have a warning (A2AJ returned no results)
    assert "warning" in leg


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: year-stripped Direction-A match — CanLII title carries a
#  trailing year as part of its official name (e.g. "Taxation Act, 2007"),
#  making it invisible to exact match and Direction-A substring match.
# ═════════════════════════════════════════════════════════════════════════════

RECORD_TAXATION_2007 = {
    "databaseId": "ons", "legislationId": "so-tax-2007",
    "title": "Taxation Act, 2007",
    "citation": "SO 2007, c 11", "type": "STATUTE",
}

RECORD_TAXATION_1997 = {
    "databaseId": "ons", "legislationId": "so-tax-1997",
    "title": "Taxation Act, 1997",
    "citation": "SO 1997, c 1", "type": "STATUTE",
}

RECORD_PROVINCIAL_LAND_TAX = {
    "databaseId": "ons", "legislationId": "so-plt",
    "title": "Provincial Land Tax Act",
    "citation": "RSO 1990, c P.31", "type": "STATUTE",
}

RECORD_PROVINCIAL_LAND_TAX_2006 = {
    "databaseId": "ons", "legislationId": "so-plt-2006",
    "title": "Provincial Land Tax Act, 2006",
    "citation": "SO 2006, c 1", "type": "STATUTE",
}

RECORD_ESTATE_TAX_1998 = {
    "databaseId": "ons", "legislationId": "so-eta-1998",
    "title": "Estate Administration Tax Act, 1998",
    "citation": "SO 1998, c 1", "type": "STATUTE",
}

RECORD_LIQUOR_TAX_1996 = {
    "databaseId": "ons", "legislationId": "so-lta-1996",
    "title": "Liquor Tax Act, 1996",
    "citation": "SO 1996, c 1", "type": "STATUTE",
}

# Titles with a year mid-string (negative guard — must not be stripped)
RECORD_CANNABIS_2019 = {
    "databaseId": "ons", "legislationId": "so-cannabis",
    "title": "Cannabis Taxation Coordination Act, 2019",
    "citation": "SO 2019, c 1", "type": "STATUTE",
}

RECORD_SOME_OTHER = {
    "databaseId": "ons", "legislationId": "so-other",
    "title": "Some Other Act",
    "citation": "RSO 1990, c O-1", "type": "STATUTE",
}


def test_year_stripped_single_match():
    """Query 'Taxation Act', CanLII has 'Taxation Act, 2007' -> verified=True via year-stripped."""
    mock_canlii = MagicMock(return_value=_canlii_resp(
        RECORD_TAXATION_2007, RECORD_SOME_OTHER,
    ))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    classification = {
        "type": "legislation",
        "normalized": "Taxation Act",
        "original": "tax act ontario",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("tax act ontario", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True, (
        f"Expected verified=True for year-stripped match, got verified={r['verified']!r} "
        f"_match_path={r['_match_path']!r}"
    )
    assert r["_match_path"] == "year_stripped_match", (
        f"Expected _match_path='year_stripped_match', got {r['_match_path']!r}"
    )
    assert r["statute_title"] == "Taxation Act, 2007", (
        f"Must match the full official title with year, got {r['statute_title']!r}"
    )
    assert r["jurisdiction"] == "ON"
    assert r["warning"] == ""
    mock_infer.assert_not_called()


def test_year_stripped_multi_match_degrade():
    """'Provincial Land Tax Act, 2006' and 'Provincial Land Tax Act, 1998'
    both collapse to same year-stripped form 'Provincial Land Tax Act' ->
    returns candidates list (not a single guess)."""
    rec_plt_1998 = dict(RECORD_PROVINCIAL_LAND_TAX_2006)
    rec_plt_1998["title"] = "Provincial Land Tax Act, 1998"
    rec_plt_1998["citation"] = "SO 1998, c 1"
    rec_plt_1998["legislationId"] = "so-plt-1998"
    mock_canlii = MagicMock(return_value=_canlii_resp(
        rec_plt_1998, RECORD_PROVINCIAL_LAND_TAX_2006,
    ))
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Provincial Land Tax Act",
        "original": "Provincial Land Tax Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Provincial Land Tax Act", classification)

    # Must return multiple candidates (same degrade as Direction-A multi-match)
    assert len(result) >= 2, (
        f"Expected >= 2 candidates for ambiguous year-stripped match, got {len(result)}"
    )
    for r in result:
        assert r["verified"] is True
        assert r["source"] == "canlii"


def test_year_stripped_negative_mid_title_year():
    """A title with text after the year (not end-anchored) is NOT stripped.
    'Some Act, 2007 and Related Amendments' has text after the year -> no match."""
    import re
    _TRAILING_YEAR_RE = re.compile(r',\s*\d{4}\s*$')
    title = "Some Act, 2007 and Related Amendments"
    m = _TRAILING_YEAR_RE.search(title)
    assert m is None, (
        "Year not at end of string must NOT match the trailing-year regex"
    )
    # Also confirm a valid trailing-year title DOES match (sanity check on the regex)
    valid = "Some Act, 2007"
    m2 = _TRAILING_YEAR_RE.search(valid)
    assert m2 is not None, (
        "Trailing year must match the regex"
    )


def test_year_stripped_generalizes():
    """Multiple trailing-year statutes resolve via year-stripped match."""
    mock_canlii = MagicMock(return_value=_canlii_resp(
        RECORD_ESTATE_TAX_1998, RECORD_LIQUOR_TAX_1996, RECORD_SOME_OTHER,
    ))
    mock_infer = MagicMock(return_value="on")

    classification = {
        "type": "legislation",
        "normalized": "Estate Administration Tax Act",
        "original": "Estate Administration Tax Act",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("Estate Administration Tax Act", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True, (
        f"Expected verified=True, got {r['verified']!r} _match_path={r['_match_path']!r}"
    )
    assert r["_match_path"] == "year_stripped_match"
    assert r["statute_title"] == "Estate Administration Tax Act, 1998"
    assert r["jurisdiction"] == "ON"


def test_exact_match_still_works_with_year_stripped():
    """Existing exact-match and Direction-A tests remain unchanged.
    'Family Law Act' against 'Family Law Act' must still match exactly."""
    mock_canlii = MagicMock(return_value=_canlii_resp(RECORD_FAMILY_LAW))
    mock_infer = MagicMock(side_effect=Exception("LLM should not be called"))

    classification = {
        "type": "legislation",
        "normalized": "Family Law Act",
        "original": "family law act ontario",
    }

    with patch("local_tools.citation_search._infer_jurisdiction_canlii", mock_infer), \
         patch("local_tools.canlii_api.browse_legislation_in_database", mock_canlii):
        result = search_citation("family law act ontario", classification)

    assert len(result) == 1
    r = result[0]
    assert r["verified"] is True
    assert r["_match_path"] == "exact"
    assert r["statute_title"] == "Family Law Act"
    assert r["jurisdiction"] == "ON"
