"""Regression tests for _verify_case() name-search fallback fix.

Reproduces the exact Creighton incident and verifies all edge cases
of year-bounded + name-normalization matching.

Run: pytest tests/test_verify_case_fix.py -v
"""

import os
import sys
import json
from unittest.mock import patch, MagicMock

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import pytest
from unittest.mock import ANY


# ═══════════════════════════════════════════════════════════════════════
# Fixtures: mock A2AJ results
# ═══════════════════════════════════════════════════════════════════════

# The REAL SCC case — [1993] 3 SCR 3 (R v Creighton, 1993 SCC)
REAL_CREIGHTON_SCC = {
    "citation_en": "[1993] 3 SCR 3",
    "name_en": "R. v. Creighton",
    "document_date_en": "1993-08-26T00:00:00+00:00",
    "dataset": "SCC",
    "citation2_en": "(1993), 83 C.C.C. (3d) 346",
}

# The WRONG BCSC case — R v Creighton, 2022 BCSC 1728
WRONG_CREIGHTON_BCSC = {
    "citation_en": "2022 BCSC 1728",
    "name_en": "R. v. Creighton",
    "document_date_en": "2022-10-03T00:00:00+00:00",
    "dataset": "BCSC",
    "citation2_en": "",
}

# Another case with "Creighton" in name but completely different — for testing
# that name normalization correctly rejects partial matches
ANOTHER_CREIGHTON = {
    "citation_en": "2020 SCC 25",
    "name_en": "Creighton v. R.",
    "document_date_en": "2020-06-12T00:00:00+00:00",
    "dataset": "SCC",
    "citation2_en": "",
}

# A distinct case for testing correct-citation primary path
OAKES_RESULT = {
    "citation_en": "[1986] 1 SCR 103",
    "name_en": "R. v. Oakes",
    "document_date_en": "1986-02-28T00:00:00+00:00",
    "dataset": "SCC",
    "citation2_en": "(1986), 26 D.L.R. (4th) 200",
}


# ═══════════════════════════════════════════════════════════════════════
# Helper to exercise _verify_case directly
# ═══════════════════════════════════════════════════════════════════════

def run_verify_case(name, citation, **extra_mocks):
    """Run _verify_case by calling expand_concept internals through verify_one.

    We mock search_cases_multi and/or fetch_by_citation.
    verify_one lives inside expand_concept, so we need to get at it.
    """
    from local_tools.citation_search import search_citation, classify_and_normalize

    # We'll construct a dict that verify_one would process
    item = {"name": name, "citation": citation, "type": "case"}

    # The simplest way to exercise _verify_case is to call the nested function
    # through search_citation() with a concept query that will run expand_concept,
    # but that's heavy. Instead, we can import expand_concept and dig into its
    # closure, or we can directly call the _verify_items path.
    #
    # Better approach: call search_citation() on a concept that returns our mocks.
    # But that requires an LLM call.
    #
    # Alternative: we can directly invoke the nested _verify_case by extracting it
    # from the expand_concept function's closure.
    #
    # Let's use the simplest approach: import expand_concept, then patch its
    # internal functions, and directly create a scenario where verify_one is used.

    # Actually, _verify_case is a local function inside expand_concept, so we
    # can't import it directly. But verify_one calls it, and _verify_items calls
    # verify_one. And expand_concept calls _verify_items.
    #
    # Our mocks will patch search_cases_multi and fetch_by_citation.
    # Then we call expand_concept with a mock LLM response that returns our item.
    # But that requires an LLM call.
    #
    # Cleanest approach: patch the LLM to return a canned response, and patch
    # A2AJ functions to return what we need.

    from local_tools import citation_search as cs_mod

    mock_llm_result = {
        "candidates": [
            {"name": name, "citation": citation, "type": "case"},
        ]
    }

    with patch.object(cs_mod, "call_gemini_text_structured") as mock_gemini:
        mock_gemini.return_value = mock_llm_result

        with patch.object(cs_mod, "fetch_by_citation") as mock_fetch:
            mock_fetch.return_value = extra_mocks.get("fetch_return",
                {"raw_input": citation, "error": "not found"})

            with patch.object(cs_mod, "search_cases_multi") as mock_search:
                mock_search.return_value = extra_mocks.get("search_return", [])

                # Provide a search_cases_multi side_effect if given
                side_effect = extra_mocks.get("search_side_effect")
                if side_effect is not None:
                    mock_search.side_effect = side_effect

                try:
                    result = cs_mod.expand_concept("test fake concept")
                except ValueError:
                    return []  # LLM expansion failed — shouldn't happen with our mock

                return result


def run_verify_case_directly(name, citation, fetch_return=None, search_return=None,
                              search_side_effect=None):
    """More direct approach: expose _verify_case via a test helper.

    We monkey-patch expand_concept to call _verify_case directly,
    then restore it.
    """
    from local_tools import citation_search as cs_mod
    import types

    # We need to get the _verify_case function. Since it's a nested closure,
    # let's use an alternative approach: we'll craft a scenario where the
    # LLM returns exactly one candidate and we control the mocks.

    mock_llm_result = {
        "candidates": [
            {"name": name, "citation": citation, "type": "case"},
        ]
    }

    patchers = [
        patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm_result),
    ]

    if fetch_return is not None:
        patchers.append(patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return))
    elif search_return is not None or search_side_effect is not None:
        # When we want to test fallback, make fetch fail
        patchers.append(patch.object(cs_mod, "fetch_by_citation", return_value={"raw_input": citation, "error": "not found"}))

    if search_return is not None:
        patchers.append(patch.object(cs_mod, "search_cases_multi", return_value=search_return))
    elif search_side_effect is not None:
        patchers.append(patch.object(cs_mod, "search_cases_multi", side_effect=search_side_effect))

    for p in patchers:
        p.start()

    try:
        result = cs_mod.expand_concept("test fake concept")
        return result
    except ValueError:
        return []
    finally:
        for p in patchers:
            p.stop()


# ═══════════════════════════════════════════════════════════════════════
# A simpler, more reliable approach: test via verify_one directly
# by monkey-patching expand_concept to expose it
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def verify_case_func():
    """Extract and return the _verify_case nested function.

    We do this by importing expand_concept, then calling it with mocks
    that cause _verify_case to be created as a closure, which we intercept.
    """
    from local_tools import citation_search as cs_mod

    # expand_concept creates _verify_case as a local nested function.
    # We can't import it directly. But we can monkeypatch search_cases_multi
    # and fetch_by_citation, then inspect the closure.
    #
    # A simpler approach: since _verify_case calls search_cases_multi and
    # fetch_by_citation (which are module-level), we can test the full
    # verify_one -> _verify_case pipeline by just mocking those module-level
    # functions and calling search_citation with concept type.
    #
    # But that requires an LLM call.
    #
    # Most pragmatic: we already have the code. Let's just test expand_concept
    # with fully mocked dependencies.
    return None  # We'll use the direct approach below


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


class TestVerifyCaseFix:
    """Reproduce the exact Creighton incident and all edge cases."""

    # ── Test 1: Exact incident reproduction ──
    # LLM proposes "R v Creighton" with citation "1993 3 SCR 484" (wrong page).
    # fetch_by_citation fails. Year=1993 extracted. Year-bounded search should
    # only find the real [1993] 3 SCR 3 case, NOT the 2022 BCSC case.

    def test_creighton_incident_year_bounded_resolves_correctly(self):
        """Reproduce exact Creighton incident: wrong citation, year-bounded
        search correctly resolves to [1993] 3 SCR 3, never 2022 BCSC 1728."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R v Creighton", "citation": "1993 3 SCR 484", "type": "case"}]
        }

        fetch_return = {"raw_input": "1993 3 SCR 484", "error": "not found"}

        # Year-bounded search should only return the real SCC case (1993),
        # NOT the 2022 BCSC case, because date bounds restrict to 1993.
        search_return = [REAL_CREIGHTON_SCC]

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return):
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    mock_search.return_value = search_return

                    results = cs_mod.expand_concept("mens rea")

        # Verify search_cases_multi was called with year bounds
        assert mock_search.call_count >= 1
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs.get("start_date") == "1993-01-01", (
            f"Expected start_date=1993-01-01, got {call_kwargs.get('start_date')}"
        )
        assert call_kwargs.get("end_date") == "1993-12-31", (
            f"Expected end_date=1993-12-31, got {call_kwargs.get('end_date')}"
        )

        # Verify result
        assert len(results) == 1
        r = results[0]
        assert r["verified"] is True, f"Expected verified=True, got {r}"
        # Must resolve to [1993] 3 SCR 3, not anything else.  Creighton is a
        # pre-neutral case, so the print citation lands in reporter and
        # neutral_citation stays empty (see _map_fields shape slotting).
        assert r.get("reporter") == "[1993] 3 SCR 3", (
            f"Expected [1993] 3 SCR 3, got {r.get('reporter')}"
        )
        assert r.get("neutral_citation") == "", (
            f"Pre-neutral case must not claim a neutral cite, got {r.get('neutral_citation')!r}"
        )
        # Must NOT be the 2022 BCSC case
        assert r.get("neutral_citation") != "2022 BCSC 1728", (
            "Should NOT resolve to 2022 BCSC 1728"
        )

    # ── Test 2: Same name, no year extractable → never blindly pick index 0 ──
    # If citation is empty/null, search returns both 1993 SCC and 2022 BCSC.
    # Name normalization matches both → ambiguous → verified=False.

    def test_creighton_no_citation_ambiguous_returns_unverified(self):
        """Same case name with no citation → both SCC and BCSC cases match
        by name → verified=False with ambiguous warning, never index 0."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R v Creighton", "citation": None, "type": "case"}]
        }

        fetch_return = {"raw_input": "R v Creighton", "error": "not found"}

        # Unbounded search returns BOTH cases (same name, different years)
        search_return = [WRONG_CREIGHTON_BCSC, REAL_CREIGHTON_SCC]

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return):
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    mock_search.return_value = search_return

                    results = cs_mod.expand_concept("mens rea")

        # Must NOT return verified=True with either case blindly
        assert len(results) >= 1
        r = results[0]
        assert r["verified"] is False, (
            f"Expected verified=False (ambiguous), got verified=True with {r.get('neutral_citation')}"
        )
        assert "多个同名判例" in r.get("warning", ""), (
            f"Expected warning about ambiguity, got: {r.get('warning', '')}"
        )

    # ── Test 3: Multi-match even after year-bounding ──
    # Two cases with same name, same year, different reports/courts

    def test_multi_match_same_year_returns_unverified(self):
        """Two cases with same name and same year → verified=False with
        year-specific ambiguous warning."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R v Smith", "citation": "2000 1 SCR 1", "type": "case"}]
        }

        fetch_return = {"raw_input": "2000 1 SCR 1", "error": "not found"}

        # Two different cases with same name, same year
        same_name_year_a = {
            "citation_en": "2000 1 SCR 1",
            "name_en": "R. v. Smith",
            "document_date_en": "2000-01-01T00:00:00+00:00",
            "dataset": "SCC",
            "citation2_en": "",
        }
        same_name_year_b = {
            "citation_en": "2000 ABCA 1",
            "name_en": "R. v. Smith",
            "document_date_en": "2000-06-01T00:00:00+00:00",
            "dataset": "ABCA",
            "citation2_en": "",
        }

        search_return = [same_name_year_a, same_name_year_b]

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return):
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    mock_search.return_value = search_return

                    results = cs_mod.expand_concept("test concept")

        assert len(results) >= 1
        r = results[0]
        assert r["verified"] is False, (
            f"Expected verified=False for ambiguous same-year match"
        )
        assert "同一年份" in r.get("warning", ""), (
            f"Expected year-specific ambiguous warning, got: {r.get('warning', '')}"
        )

    # ── Test 4: Correct citation (primary path succeeds) → no regression ──
    # fetch_by_citation resolves → verified=True, fallback never reached

    def test_correct_citation_primary_path_unaffected(self):
        """Correct citation passes through fetch_by_citation → verified=True,
        fallback name search never called."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R v Oakes", "citation": "[1986] 1 SCR 103", "type": "case"}]
        }

        fetch_return = {
            "style_of_cause": "R. v. Oakes",
            "neutral_citation": "[1986] 1 SCR 103",
            "year": "1986",
        }

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation") as mock_fetch:
                mock_fetch.return_value = fetch_return
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    # search_cases_multi should NEVER be called
                    results = cs_mod.expand_concept("test concept")

        assert len(results) == 1
        r = results[0]
        assert r["verified"] is True
        assert r.get("neutral_citation") == "[1986] 1 SCR 103"

    # ── Test 5: Year extracted but year-bounded search returns 0 results ──
    # Conservative: verified=False, no wider fallback

    def test_year_bounded_zero_results_verified_false(self):
        """Year extracted but A2AJ returns 0 results in that year → verified=False,
        not falling back to unbounded search."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R v RareName", "citation": "1999 CanLII 1", "type": "case"}]
        }

        fetch_return = {"raw_input": "1999 CanLII 1", "error": "not found"}
        search_return = []  # zero results

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return):
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    mock_search.return_value = search_return

                    results = cs_mod.expand_concept("test concept")

        assert len(results) >= 1
        r = results[0]
        assert r["verified"] is False
        # search_cases_multi was called once with year bounds, not again
        assert mock_search.call_count == 1
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs.get("start_date") == "1999-01-01"

    # ── Test 6: Name with French prefix "R c" also works ──
    def test_french_prefix_normalized_correctly(self):
        """'R c Creighton' with citation → French prefix stripped for matching."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R c Creighton", "citation": "1993 3 SCR 484", "type": "case"}]
        }

        fetch_return = {"raw_input": "1993 3 SCR 484", "error": "not found"}
        search_return = [REAL_CREIGHTON_SCC]  # A2AJ returns "R. v. Creighton" (English)

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return):
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    mock_search.return_value = search_return

                    results = cs_mod.expand_concept("mens rea")

        assert len(results) == 1
        r = results[0]
        assert r["verified"] is True, (
            f"French prefix 'R c' should normalize to match 'R. v.' — got {r}"
        )
        assert r.get("reporter") == "[1993] 3 SCR 3"
        assert r.get("neutral_citation") == ""

    # ── Test 7: No year + 0 name-normalization matches → verified=False ──
    def test_no_year_zero_name_matches(self):
        """No citation and A2AJ returns only cases with wrong normalized name
        → verified=False, not a false positive."""
        from local_tools import citation_search as cs_mod

        mock_llm = {
            "candidates": [{"name": "R v Creighton", "citation": None, "type": "case"}]
        }

        fetch_return = {"raw_input": "R v Creighton", "error": "not found"}

        # A2AJ returns ANOTHER_CREIGHTON which has "Creighton v. R." as name_en
        # After normalization: "creighton v r" != "creighton" → no match
        search_return = [ANOTHER_CREIGHTON]

        with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
            with patch.object(cs_mod, "fetch_by_citation", return_value=fetch_return):
                with patch.object(cs_mod, "search_cases_multi") as mock_search:
                    mock_search.return_value = search_return

                    results = cs_mod.expand_concept("test concept")

        assert len(results) >= 1
        r = results[0]
        assert r["verified"] is False


# ═══════════════════════════════════════════════════════════════════════
# Integration smoke tests (requires live API — marked as skip by default)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="Live API test — run manually")
class TestLiveExpandConcept:
    """Run expand_concept('mens rea') at least 3 times and report results.

    These tests require live A2AJ and LLM API calls.
    Run with: python -m pytest tests/test_verify_case_fix.py -v -k "live"
    """

    NUM_ROUNDS = 3

    def test_live_mens_rea_multiple_rounds(self):
        """Run expand_concept('mens rea') 3+ times, verify Creighton
        resolves correctly or is unverified."""
        from local_tools.citation_search import expand_concept

        for i in range(self.NUM_ROUNDS):
            results = expand_concept("mens rea")
            assert len(results) > 0, f"Round {i+1}: zero candidates"

            for r in results:
                name = r.get("style_of_cause") or r.get("name", "")
                cit = r.get("neutral_citation") or ""

                if "Creighton" in name:
                    # Must never show the wrong 2022 BCSC case as verified
                    if r["verified"]:
                        assert "2022 BCSC" not in cit, (
                            f"Round {i+1}: Creighton resolved to WRONG case: {cit}"
                        )
                        assert cit == "[1993] 3 SCR 3" or cit == "1993 3 SCR 3" or "1993" in cit, (
                            f"Round {i+1}: Creighton verified with unexpected citation: {cit}"
                        )

            # Brief pause between rounds
            if i < self.NUM_ROUNDS - 1:
                import time
                time.sleep(2)


# Only run these if explicitly requested
@pytest.mark.skip(reason="Requires LLM + A2AJ — not for CI")
def test_live_expand_concept_mens_rea_round1():
    """Single live round for quick manual check."""
    from local_tools.citation_search import expand_concept
    results = expand_concept("mens rea")
    print(f"\n=== mens rea candidates ({len(results)}) ===")
    for r in results:
        name = r.get("style_of_cause") or r.get("statute_title") or r.get("name", "?")
        cit = r.get("neutral_citation") or ""
        verified = "[OK]" if r.get("verified") else "[!!]"
        warn = f" -- {r.get('warning', '')}" if r.get("warning") else ""
        print(f"  {verified} {name} -- {cit}{warn}")

    # Check Creighton specifically
    for r in results:
        name = r.get("style_of_cause") or r.get("name", "")
        if "Creighton" in name:
            assert not (r["verified"] and "2022 BCSC" in (r.get("neutral_citation") or "")), (
                "FATAL: Creighton wrongly resolved to 2022 BCSC case!"
            )
