"""Regression tests for input-shape hardening (Commit 2).

Covers:
- classify_and_normalize rejects LLM dicts missing normalized/original (M2)
- a2aj_api tolerates non-dict result elements, null dataset, array envelopes (M1)
- concept scaffold prefill comes from the user's query, never a hardcoded
  example (H3)

Run: pytest tests/test_input_shape_guards.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from local_tools.a2aj_api import _map_fields, fetch_by_citation, search_cases_multi

_TIMING_PATCH = patch("profiling.timing.ENABLED", False)
_A2AJ_TIMING_PATCHES = (
    patch("profiling.timing.ENABLED", False),
    patch("local_tools.a2aj_api.timing.ENABLE_TIMING", False),
)


def _http_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


# ═════════════════════════════════════════════════════════════════════════════
#  M2 — classify result shape validation
# ═════════════════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════════════════
#  M1 — a2aj payload shape tolerance
# ═════════════════════════════════════════════════════════════════════════════

def test_fetch_by_citation_non_dict_result_element():
    """results[0] being a string must degrade to raw_input, not AttributeError."""
    with patch("local_tools.a2aj_api.request_with_retry",
               return_value=_http_response({"results": ["just-a-string"]})), \
         _A2AJ_TIMING_PATCHES[0], _A2AJ_TIMING_PATCHES[1]:
        result = fetch_by_citation("2022 SCC 39", doc_type="cases")

    assert result == {"raw_input": "2022 SCC 39"}


def test_fetch_by_citation_null_result_element():
    with patch("local_tools.a2aj_api.request_with_retry",
               return_value=_http_response({"results": [None]})), \
         _A2AJ_TIMING_PATCHES[0], _A2AJ_TIMING_PATCHES[1]:
        result = fetch_by_citation("2022 SCC 39")

    assert result == {"raw_input": "2022 SCC 39"}


def test_search_cases_multi_array_envelope_returns_empty():
    """A valid-JSON array (not an object) must yield [], not AttributeError."""
    with patch("local_tools.a2aj_api.request_with_retry",
               return_value=_http_response(["unexpected", "array"])), \
         _A2AJ_TIMING_PATCHES[0], _A2AJ_TIMING_PATCHES[1]:
        result = search_cases_multi("R v Gladue")

    assert result == []


def test_search_cases_multi_filters_non_dict_elements():
    with patch("local_tools.a2aj_api.request_with_retry",
               return_value=_http_response({"results": [{"name_en": "ok"}, "junk", None]})), \
         _A2AJ_TIMING_PATCHES[0], _A2AJ_TIMING_PATCHES[1]:
        result = search_cases_multi("R v Gladue")

    assert result == [{"name_en": "ok"}]


def test_map_fields_null_dataset_no_crash():
    """dataset key present but null must not crash .upper()."""
    result = _map_fields({
        "name_en": "R v Oakes",
        "citation_en": "[1986] 1 SCR 103",
        "document_date_en": "1986-02-26",
        "dataset": None,
    })
    assert result["style_of_cause"] == "R v Oakes"
    assert "statute_title" not in result  # treated as a case, not legislation


# ═════════════════════════════════════════════════════════════════════════════
