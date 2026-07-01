"""Unit tests for _bracket_reporter_year — deterministic bracket normalization
for bare reporter citations, plus structural tests of search_citation()
showing the bracket-adjusted string reaches A2AJ.

This file self-tests when run via `pytest tests/test_citation_brackets.py -v` or
`python tests/test_citation_brackets.py`.  It does NOT make live HTTP calls.
"""

import os
import sys
from unittest.mock import MagicMock, patch

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)


# ═══════════════════════════════════════════════════════════════════════
# Unit tests for _bracket_reporter_year — pure, deterministic, no I/O
# ═══════════════════════════════════════════════════════════════════════

def test_reporter_bare_year_adds_brackets():
    """'1986 1 SCR 103' → '[1986] 1 SCR 103'."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("1986 1 SCR 103")
    assert result == "[1986] 1 SCR 103", (
        f"Expected '[1986] 1 SCR 103', got {result!r}"
    )


def test_reporter_bare_year_volume_page():
    """'1999 1 SCR 688' → '[1999] 1 SCR 688'."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("1999 1 SCR 688")
    assert result == "[1999] 1 SCR 688", (
        f"Expected '[1999] 1 SCR 688', got {result!r}"
    )


def test_reporter_multi_volume_digit():
    """'2009 2 FCR 488' → '[2009] 2 FCR 488'."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("2009 2 FCR 488")
    assert result == "[2009] 2 FCR 488", (
        f"Expected '[2009] 2 FCR 488', got {result!r}"
    )


def test_already_bracketed_unchanged():
    """'[1986] 1 SCR 103' → unchanged (no double-bracketing)."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("[1986] 1 SCR 103")
    assert result == "[1986] 1 SCR 103", (
        f"Expected '[1986] 1 SCR 103', got {result!r}"
    )


def test_neutral_citation_unchanged():
    """'2022 SCC 39' → unchanged (regression guard)."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("2022 SCC 39")
    assert result == "2022 SCC 39", (
        f"Expected '2022 SCC 39', got {result!r}"
    )


def test_neutral_onca_unchanged():
    """'2009 ONCA 477' → unchanged (regression guard)."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("2009 ONCA 477")
    assert result == "2009 ONCA 477", (
        f"Expected '2009 ONCA 477', got {result!r}"
    )


def test_ambiguous_no_volume_unchanged():
    """Ambiguous string missing the numeric volume → unchanged (degrade)."""
    from local_tools.citation_search import _bracket_reporter_year
    # Neutral-like but with extra text
    result = _bracket_reporter_year("2022 SCC")
    assert result == "2022 SCC", (
        f"Expected '2022 SCC', got {result!r}"
    )


def test_garbage_unchanged():
    """Garbage string that matches neither pattern → unchanged (degrade)."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("just some legal text")
    assert result == "just some legal text", (
        f"Expected 'just some legal text', got {result!r}"
    )


def test_empty_string_unchanged():
    """Empty string → unchanged."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("")
    assert result == "", (
        f"Expected '', got {result!r}"
    )


def test_reporter_with_extra_whitespace():
    """Extra whitespace still matches — strip before pattern."""
    from local_tools.citation_search import _bracket_reporter_year
    result = _bracket_reporter_year("  1986  1  SCR 103  ")
    assert result == "[1986] 1 SCR 103", (
        f"Expected '[1986] 1 SCR 103', got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Structural tests — search_citation citation_number route
# Verifies the bracketed string reaches A2AJ, not the bare original.
# ═══════════════════════════════════════════════════════════════════════

_ANY_CASE_RESULT = {
    "results": [{
        "citation_en": "[1986] 1 SCR 103",
        "name_en": "R. v. Oakes",
        "document_date_en": "1986-02-28T00:00:00+00:00",
        "dataset": "SCC",
    }]
}


def test_bare_reporter_flows_with_brackets():
    """'1986 1 SCR 103' — the bill of materials reaching A2AJ must have brackets."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "1986 1 SCR 103",
        "original": "1986 1 scr 103",
    }

    mock_requests = MagicMock()
    mock_requests.get.return_value.json.return_value = _ANY_CASE_RESULT
    # Make raise_for_status a no-op
    mock_requests.get.return_value.raise_for_status.return_value = None

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Oakes",
            "neutral_citation": "[1986] 1 SCR 103",
            "year": "1986",
        }
        result = search_citation("1986 1 scr 103", classification)

    # fetch_by_citation must have been called with the bracketed string
    mock_fetch.assert_called_once()
    call_arg = mock_fetch.call_args[0][0]
    assert call_arg == "[1986] 1 SCR 103", (
        f"fetch_by_citation called with {call_arg!r}, expected '[1986] 1 SCR 103'"
    )
    assert len(result) == 1
    assert result[0].get("verified") is True
    assert result[0].get("style_of_cause") == "R. v. Oakes"


def test_neutral_citation_passes_unchanged_to_a2aj():
    """'2022 SCC 39' — neutral citation must reach A2AJ WITHOUT brackets."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "2022 SCC 39",
        "original": "2022 SCC 39",
    }

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Sharma",
            "neutral_citation": "2022 SCC 39",
            "year": "2022",
        }
        result = search_citation("2022 SCC 39", classification)

    mock_fetch.assert_called_once()
    call_arg = mock_fetch.call_args[0][0]
    assert call_arg == "2022 SCC 39", (
        f"fetch_by_citation called with {call_arg!r}, expected '2022 SCC 39'"
    )
    assert result[0].get("verified") is True


def test_already_bracketed_passes_unchanged():
    """'[1986] 1 SCR 103' — already-bracketed citation passes through as-is."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "[1986] 1 SCR 103",
        "original": "[1986] 1 SCR 103",
    }

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Oakes",
            "neutral_citation": "[1986] 1 SCR 103",
            "year": "1986",
        }
        result = search_citation("[1986] 1 SCR 103", classification)

    mock_fetch.assert_called_once()
    call_arg = mock_fetch.call_args[0][0]
    assert call_arg == "[1986] 1 SCR 103", (
        f"fetch_by_citation called with {call_arg!r}, expected '[1986] 1 SCR 103'"
    )


# ═══════════════════════════════════════════════════════════════════════
# Step 2 — Input cleanup (trailing period stripping, whitespace collapse)
# ═══════════════════════════════════════════════════════════════════════

def test_trailing_period_stripped_before_bracket():
    """"1986 1 scr 103." → trailing period stripped → bracket regex matches → resolves."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "1986 1 SCR 103.",
        "original": "1986 1 scr 103.",
    }

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Oakes",
            "neutral_citation": "[1986] 1 SCR 103",
            "year": "1986",
        }
        result = search_citation("1986 1 scr 103.", classification)

    mock_fetch.assert_called_once()
    call_arg = mock_fetch.call_args[0][0]
    assert call_arg == "[1986] 1 SCR 103", (
        f"fetch_by_citation called with {call_arg!r}, expected '[1986] 1 SCR 103'"
    )
    assert result[0].get("verified") is True


def test_bare_reporter_without_period_unchanged():
    """"1986 1 scr 103" — unchanged bracket behavior, unaffected by Step 2."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "1986 1 SCR 103",
        "original": "1986 1 scr 103",
    }

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Oakes",
            "neutral_citation": "[1986] 1 SCR 103",
            "year": "1986",
        }
        result = search_citation("1986 1 scr 103", classification)

    mock_fetch.assert_called_once()
    call_arg = mock_fetch.call_args[0][0]
    assert call_arg == "[1986] 1 SCR 103"


def test_trailing_period_neutral_unchanged():
    """"2022 SCC 39." — trailing period stripped but neutral format doesn't bracket."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "2022 SCC 39.",
        "original": "2022 SCC 39.",
    }

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Sharma",
            "neutral_citation": "2022 SCC 39",
            "year": "2022",
        }
        result = search_citation("2022 SCC 39.", classification)

    mock_fetch.assert_called_once()
    call_arg = mock_fetch.call_args[0][0]
    # Period was stripped but neutral doesn't match bracket regex
    assert call_arg == "2022 SCC 39"


# ═══════════════════════════════════════════════════════════════════════
# Step 3 — Case-route pinpoint extraction (classifier-gated)
# ═══════════════════════════════════════════════════════════════════════

def test_case_route_pinpoint_extracted():
    """"r v leo at para 2" → pinpoint="at para 2", search query strips it."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "case_name",
        "normalized": "R v leo at para 2",
        "original": "r v leo at para 2",
    }

    mock_result = [{
        "name_en": "R. v. Leo-Mensah",
        "citation_en": "2010 ONCA 139",
        "document_date_en": "2010-06-01T00:00:00+00:00",
        "dataset": "ONCA",
        "citation2_en": "",
    }]

    with patch("local_tools.citation_search.search_cases_multi") as mock_search:
        mock_search.return_value = mock_result
        result = search_citation("r v leo at para 2", classification)

    # Search must be called with query WITHOUT "at para 2"
    call_query = mock_search.call_args[0][0]
    assert "at para 2" not in call_query, (
        f"search query should not contain pinpoint, got {call_query!r}"
    )
    assert "leo" in call_query.lower()

    # Result must include pinpoint
    assert len(result) == 1
    assert result[0].get("pinpoint") == "at para 2"


def test_case_route_pinpoint_paras_range():
    """"r v sharma at paras 10-15" → pinpoint="at paras 10-15"."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "case_name",
        "normalized": "R v sharma at paras 10-15",
        "original": "r v sharma at paras 10-15",
    }

    mock_result = [{
        "name_en": "R. v. Sharma",
        "citation_en": "2022 SCC 39",
        "document_date_en": "2022-11-01T00:00:00+00:00",
        "dataset": "SCC",
        "citation2_en": "",
    }]

    with patch("local_tools.citation_search.search_cases_multi") as mock_search:
        mock_search.return_value = mock_result
        result = search_citation("r v sharma at paras 10-15", classification)

    call_query = mock_search.call_args[0][0]
    assert "at paras 10-15" not in call_query
    assert result[0].get("pinpoint") == "at paras 10-15"


def test_case_route_no_pinpoint_regression():
    """"R v Oakes" → no pinpoint extracted, field stays empty."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "case_name",
        "normalized": "R v Oakes",
        "original": "R v Oakes",
    }

    mock_result = [{
        "name_en": "R. v. Oakes",
        "citation_en": "[1986] 1 SCR 103",
        "document_date_en": "1986-02-28T00:00:00+00:00",
        "dataset": "SCC",
        "citation2_en": "",
    }]

    with patch("local_tools.citation_search.search_cases_multi") as mock_search:
        mock_search.return_value = mock_result
        result = search_citation("R v Oakes", classification)

    assert result[0].get("pinpoint") is None
    # Verify the search query is unchanged
    call_query = mock_search.call_args[0][0]
    assert "Oakes" in call_query


def test_case_route_trailing_period_no_pinpoint():
    """"R v Oakes." → trailing period not a pinpoint; no extraction."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "case_name",
        "normalized": "R v Oakes",
        "original": "R v Oakes",
    }

    mock_result = [{
        "name_en": "R. v. Oakes",
        "citation_en": "[1986] 1 SCR 103",
        "document_date_en": "1986-02-28T00:00:00+00:00",
        "dataset": "SCC",
        "citation2_en": "",
    }]

    with patch("local_tools.citation_search.search_cases_multi") as mock_search:
        mock_search.return_value = mock_result
        result = search_citation("R v Oakes.", classification)

    assert result[0].get("pinpoint") is None


def test_case_route_trailing_period_with_pinpoint():
    """"R v Leo at para 2." → trailing period stripped → pinpoint='at para 2' → search='R v Leo'."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "case_name",
        "normalized": "R v Leo at para 2.",  # LLM may or may not strip the period
        "original": "R v Leo at para 2.",
    }

    mock_result = [{
        "name_en": "R. v. Leo-Mensah",
        "citation_en": "2010 ONCA 139",
        "document_date_en": "2010-06-01T00:00:00+00:00",
        "dataset": "ONCA",
        "citation2_en": "",
    }]

    with patch("local_tools.citation_search.search_cases_multi") as mock_search:
        mock_search.return_value = mock_result
        result = search_citation("R v Leo at para 2.", classification)

    # Pinpoint must be extracted
    assert result[0].get("pinpoint") == "at para 2"

    # Search query must NOT contain pinpoint or trailing period
    call_query = mock_search.call_args[0][0]
    assert "at para 2" not in call_query
    assert not call_query.rstrip().endswith('.')


def test_citation_number_no_pinpoint():
    """"2022 SCC 39" — citation_number route bypasses case pinpoint extraction entirely."""
    from local_tools.citation_search import search_citation

    classification = {
        "type": "citation_number",
        "normalized": "2022 SCC 39",
        "original": "2022 SCC 39",
    }

    with patch("local_tools.citation_search.fetch_by_citation") as mock_fetch:
        mock_fetch.return_value = {
            "style_of_cause": "R. v. Sharma",
            "neutral_citation": "2022 SCC 39",
            "year": "2022",
        }
        result = search_citation("2022 SCC 39", classification)

    assert result[0].get("pinpoint") is None


# ═══════════════════════════════════════════════════════════════════════
# CLI runner (for environments without pytest)
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    failures = []
    tests = [
        ("test_reporter_bare_year_adds_brackets", test_reporter_bare_year_adds_brackets),
        ("test_reporter_bare_year_volume_page", test_reporter_bare_year_volume_page),
        ("test_reporter_multi_volume_digit", test_reporter_multi_volume_digit),
        ("test_already_bracketed_unchanged", test_already_bracketed_unchanged),
        ("test_neutral_citation_unchanged", test_neutral_citation_unchanged),
        ("test_neutral_onca_unchanged", test_neutral_onca_unchanged),
        ("test_ambiguous_no_volume_unchanged", test_ambiguous_no_volume_unchanged),
        ("test_garbage_unchanged", test_garbage_unchanged),
        ("test_empty_string_unchanged", test_empty_string_unchanged),
        ("test_reporter_with_extra_whitespace", test_reporter_with_extra_whitespace),
        ("test_bare_reporter_flows_with_brackets", test_bare_reporter_flows_with_brackets),
        ("test_neutral_citation_passes_unchanged_to_a2aj", test_neutral_citation_passes_unchanged_to_a2aj),
        ("test_already_bracketed_passes_unchanged", test_already_bracketed_passes_unchanged),
        # Step 2 — Input cleanup
        ("test_trailing_period_stripped_before_bracket", test_trailing_period_stripped_before_bracket),
        ("test_bare_reporter_without_period_unchanged", test_bare_reporter_without_period_unchanged),
        ("test_trailing_period_neutral_unchanged", test_trailing_period_neutral_unchanged),
        # Step 3 — Case-route pinpoint extraction
        ("test_case_route_pinpoint_extracted", test_case_route_pinpoint_extracted),
        ("test_case_route_pinpoint_paras_range", test_case_route_pinpoint_paras_range),
        ("test_case_route_no_pinpoint_regression", test_case_route_no_pinpoint_regression),
        ("test_case_route_trailing_period_no_pinpoint", test_case_route_trailing_period_no_pinpoint),
        ("test_case_route_trailing_period_with_pinpoint", test_case_route_trailing_period_with_pinpoint),
        ("test_citation_number_no_pinpoint", test_citation_number_no_pinpoint),
    ]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failures.append(name)
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failures.append(name)

    print()
    if failures:
        print(f"FAILED: {len(failures)}/{len(tests)} tests")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
        sys.exit(0)
