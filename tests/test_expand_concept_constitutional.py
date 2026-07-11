"""Regression tests for the constitutional-title verification shortcut in verify_one().

The shortcut (inside expand_concept() → verify_one() → legislation branch)
normalizes legislation candidate names and checks against the
``_CONSTITUTIONAL_CANONICAL`` closed set before falling through to
``_verify_legislation()`` (A2AJ).

Approach (a): call expand_concept() with LLM and A2AJ calls fully mocked
so only the verify_one() logic path executes deterministically.  This avoids
touching any production logic or extracting nested functions.

Run:  pytest tests/test_expand_concept_constitutional.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock, ANY
from local_tools.citation_search import expand_concept, _CONSTITUTIONAL_CANONICAL


def _mock_gemini_response(candidates: list) -> dict:
    """Build a mock Gemini structured response wrapping *candidates*."""
    return {"candidates": candidates}


def _candidate(name: str, ctype: str, citation: str | None = None) -> dict:
    """Build a single LLM candidate dict in the shape expand_concept expects."""
    d: dict = {"name": name, "type": ctype}
    if citation is not None:
        d["citation"] = citation
    return d


def _a2aj_empty_response() -> MagicMock:
    """A mock HTTP response whose .json() returns empty results (A2AJ miss)."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"results": []}
    return resp


# ══════════════════════════════════════════════════════════════════════
#  Tests 1-4: Constitutional shortcut matches
# ══════════════════════════════════════════════════════════════════════

def test_1_charter_with_pinpoint():
    """Charter + pinpoint → verified=True, correct title + pinpoint, no A2AJ."""
    candidates = [_candidate("Canadian Charter of Rights and Freedoms, s 2", "legislation")]

    with patch("local_tools.citation_search.call_gemini_text_structured") as mock_gemini, \
         patch("local_tools.utils.request_with_retry") as mock_a2aj:

        mock_gemini.return_value = _mock_gemini_response(candidates)
        result = expand_concept("freedom of expression")

    # A2AJ must NOT be reached (constitutional shortcut)
    mock_a2aj.assert_not_called()

    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    item = result[0]
    assert item.get("verified") is True, f"Expected verified=True, got {item.get('verified')!r}"
    assert item.get("statute_title") == "Canadian Charter of Rights and Freedoms", (
        f"Wrong statute_title: {item.get('statute_title')!r}"
    )
    assert item.get("pinpoint") and "s 2" in item["pinpoint"], (
        f"Expected pinpoint containing 's 2', got {item.get('pinpoint')!r}"
    )
    # Ad-hoc LLM fields must be removed (same cleanup as non-shortcut path)
    assert "name" not in item, f"ad-hoc 'name' must be removed, got {item.get('name')!r}"
    assert "neutral_citation" not in item


def test_2_constitution_act_1867_no_pinpoint():
    """Constitution Act, 1867 without pinpoint → verified=True, no A2AJ."""
    candidates = [_candidate("Constitution Act, 1867", "legislation")]

    with patch("local_tools.citation_search.call_gemini_text_structured") as mock_gemini, \
         patch("local_tools.utils.request_with_retry") as mock_a2aj:

        mock_gemini.return_value = _mock_gemini_response(candidates)
        result = expand_concept("constitution act 1867")

    mock_a2aj.assert_not_called()

    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    item = result[0]
    assert item.get("verified") is True
    assert item.get("statute_title") == "Constitution Act, 1867", (
        f"Wrong statute_title: {item.get('statute_title')!r}"
    )
    # No pinpoint expected
    assert item.get("pinpoint") is None or item["pinpoint"] == "", (
        f"Unexpected pinpoint: {item.get('pinpoint')!r}"
    )


def test_3_constitution_act_1982_with_pinpoint():
    """Constitution Act, 1982 + pinpoint → verified=True, correct title + pinpoint, no A2AJ."""
    candidates = [_candidate("Constitution Act, 1982, s 35", "legislation")]

    with patch("local_tools.citation_search.call_gemini_text_structured") as mock_gemini, \
         patch("local_tools.utils.request_with_retry") as mock_a2aj:

        mock_gemini.return_value = _mock_gemini_response(candidates)
        result = expand_concept("aboriginal rights")

    mock_a2aj.assert_not_called()

    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    item = result[0]
    assert item.get("verified") is True
    assert item.get("statute_title") == "Constitution Act, 1982", (
        f"Wrong statute_title: {item.get('statute_title')!r}"
    )
    assert item.get("pinpoint") and "s 35" in item["pinpoint"], (
        f"Expected pinpoint containing 's 35', got {item.get('pinpoint')!r}"
    )


def test_4_canada_act_1982():
    """Canada Act 1982 → verified=True, no A2AJ."""
    candidates = [_candidate("Canada Act 1982", "legislation")]

    with patch("local_tools.citation_search.call_gemini_text_structured") as mock_gemini, \
         patch("local_tools.utils.request_with_retry") as mock_a2aj:

        mock_gemini.return_value = _mock_gemini_response(candidates)
        result = expand_concept("canada act 1982")

    mock_a2aj.assert_not_called()

    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    item = result[0]
    assert item.get("verified") is True
    assert item.get("statute_title") == "Canada Act 1982", (
        f"Wrong statute_title: {item.get('statute_title')!r}"
    )


# ══════════════════════════════════════════════════════════════════════
#  Test 5: Non-constitutional legislation still reaches A2AJ
# ══════════════════════════════════════════════════════════════════════

def test_5_non_constitutional_falls_through_to_a2aj():
    """Non-constitutional legislation → A2AJ IS called, verified=False when A2AJ misses."""
    candidates = [_candidate("Code civil du Québec, art 1457", "legislation")]

    with patch("local_tools.citation_search.call_gemini_text_structured") as mock_gemini, \
         patch("local_tools.utils.request_with_retry", return_value=_a2aj_empty_response()) as mock_a2aj:

        mock_gemini.return_value = _mock_gemini_response(candidates)
        result = expand_concept("extra-contractual liability")

    # A2AJ MUST be reached (non-constitutional → no shortcut)
    mock_a2aj.assert_called()

    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    item = result[0]
    assert item.get("verified") is False, (
        f"Expected verified=False (A2AJ returned empty), got {item.get('verified')!r}"
    )


# ══════════════════════════════════════════════════════════════════════
#  Test 6: Case-type candidates are unaffected
# ══════════════════════════════════════════════════════════════════════

def test_6_case_candidate_unaffected():
    """Case-type candidate → goes through _verify_case (not the constitutional shortcut)."""
    candidates = [_candidate("R v Gladue", "case", "[1999] 1 SCR 688")]

    with patch("local_tools.citation_search.call_gemini_text_structured") as mock_gemini, \
         patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:

        mock_gemini.return_value = _mock_gemini_response(candidates)
        # Simulate a successful A2AJ case verification
        mock_fetch.return_value = {
            "style_of_cause": "R v Gladue",
            "neutral_citation": "[1999] 1 SCR 688",
        }
        result = expand_concept("gladue principle")

    # fetch_by_citation MUST be called (case verification path)
    mock_fetch.assert_called_once_with("[1999] 1 SCR 688")

    assert len(result) == 1, f"Expected 1 candidate, got {len(result)}"
    item = result[0]
    assert item.get("verified") is True, (
        f"Expected verified=True from case path, got {item.get('verified')!r}"
    )
    # Case candidate should have style_of_cause (from _verify_case result)
    assert item.get("style_of_cause") == "R v Gladue", (
        f"Wrong style_of_cause: {item.get('style_of_cause')!r}"
    )
