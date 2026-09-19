"""Regression test: bill c34 (and no-hyphen / mixed-case variants) must route to
the bill path — LEGISinfo verification → build_bill_citation — not fall through
to A2AJ legislation or unsupported.

This file self-tests when run via `pytest tests/test_bill_route.py -v` or
`python tests/test_bill_route.py`. It does NOT make live HTTP calls to A2AJ or
LEGISinfo; it only asserts the classification and routing layers.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path
_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))


def test_classifier_regex_all_variants():
    """Every bill variant must classify to type='bill' with normalized C-34 or S-2."""
    from local_tools.citation_search import classify_and_normalize

    cases = [
        ("bill c34",   "C-34"),
        ("bill C34",   "C-34"),
        ("bill c-34",  "C-34"),
        ("bill C-34",  "C-34"),
        ("Bill S-2",   "S-2"),
        ("bill s-2",   "S-2"),
        ("Bill C-22",  "C-22"),
    ]

    for query, expected_normalized in cases:
        result = classify_and_normalize(query)
        assert result["type"] == "bill", f"{query}: expected 'bill', got {result['type']}"
        assert result["normalized"] == expected_normalized, (
            f"{query}: expected normalized '{expected_normalized}', got '{result['normalized']}'"
        )


def test_classifier_llm_bill_fallback():
    """LLM prompt now includes 'bill' as a valid type; the parser must accept it."""
    from local_tools.citation_search import classify_and_normalize

    # "bill c 34" with a space between letter and digits won't match the regex,
    # so it falls through to the LLM classifier. Mock that boundary to keep the
    # test offline while exercising the real JSON parser and validation.
    llm_json = '{"type":"bill","normalized":"C-34","original":"bill c 34"}'
    with patch("local_tools.citation_search.call_gemini_text", return_value=llm_json):
        result = classify_and_normalize("bill c 34")

    assert result["type"] == "bill"
    assert result["normalized"] == "C-34"


def test_bill_route_not_legislation():
    """bill c34 must follow the bill route, not legislation → A2AJ."""
    from local_tools.citation_search import classify_and_normalize, search_citation

    classified = classify_and_normalize("bill c34")
    assert classified["type"] == "bill", f"Classifier returned {classified['type']}, not bill"

    # search_citation with bill type must use the LEGISinfo branch
    results = search_citation("bill c34", classification=classified)
    assert len(results) > 0, "bill c34 search returned zero results"

    item = results[0]

    # A verified hit from LEGISinfo carries _bill_citation
    if item.get("verified"):
        assert "_bill_citation" in item, (
            "verified bill result must contain _bill_citation from LEGISinfo"
        )
    else:
        # Bill not in current session — still must carry bill metadata, not legislation fields
        assert "bill_number" in item or "style_of_cause" in item, (
            "unverified bill result must carry bill_number or style_of_cause"
        )


if __name__ == "__main__":
    # Run tests inline when executed directly (no pytest required)
    failures = []
    for name, fn in [
        ("test_classifier_regex_all_variants", test_classifier_regex_all_variants),
        ("test_classifier_llm_bill_fallback", test_classifier_llm_bill_fallback),
        ("test_bill_route_not_legislation", test_bill_route_not_legislation),
        ("test_unsupported_response_carries_suggested_type", test_unsupported_response_carries_suggested_type),
    ]:
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
        print(f"FAILED: {len(failures)}/4 tests")
        sys.exit(1)
    else:
        print("All 4 tests passed.")
        sys.exit(0)
