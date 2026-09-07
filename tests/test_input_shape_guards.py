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

import pytest

from local_tools.a2aj_api import _map_fields, fetch_by_citation, search_cases_multi
from local_tools.citation_search import classify_and_normalize

_TIMING_PATCH = patch("profiling.timing.ENABLED", False)
_A2AJ_TIMING_PATCHES = (
    patch("profiling.timing.ENABLED", False),
    patch("local_tools.a2aj_api.timing.ENABLE_TIMING", False),
)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """The whole suite shares one in-process per-IP limiter bucket; keep these
    requests out of that budget."""
    with patch("api.main.rate_limiter.check", return_value=True):
        yield


def _http_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


# ═════════════════════════════════════════════════════════════════════════════
#  M2 — classify result shape validation
# ═════════════════════════════════════════════════════════════════════════════

def test_classify_missing_normalized_falls_back_to_deepseek():
    """Gemini says the type but omits normalized/original — must fall back
    instead of crashing downstream with KeyError."""
    good = '{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'
    with patch("local_tools.citation_search.call_gemini_text", return_value='{"type": "case_name"}'), \
         patch("local_tools.citation_search.ask_deepseek", return_value=good), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


def test_classify_null_normalized_falls_back():
    with patch("local_tools.citation_search.call_gemini_text", return_value=(
        '{"type": "bill", "normalized": null, "original": "bill c-22"}'
    )), \
         patch("local_tools.citation_search.ask_deepseek", return_value=(
             '{"type": "bill", "normalized": "C-22", "original": "bill c-22"}'
         )), \
         _TIMING_PATCH:
        result = classify_and_normalize("bill c-22")

    assert result["normalized"] == "C-22"


def test_classify_empty_normalized_falls_back():
    with patch("local_tools.citation_search.call_gemini_text", return_value=(
        '{"type": "legislation", "normalized": "  ", "original": "Criminal Code"}'
    )), \
         patch("local_tools.citation_search.ask_deepseek", return_value=(
             '{"type": "legislation", "normalized": "Criminal Code", "original": "Criminal Code"}'
         )), \
         _TIMING_PATCH:
        result = classify_and_normalize("Criminal Code")

    assert result["normalized"] == "Criminal Code"


def test_classify_both_shapes_bad_returns_default():
    """Both LLMs return type-only dicts → final case_name fallback (no crash)."""
    with patch("local_tools.citation_search.call_gemini_text", return_value='{"type": "concept"}'), \
         patch("local_tools.citation_search.ask_deepseek", return_value='{"type": "concept"}'), \
         _TIMING_PATCH:
        result = classify_and_normalize("gladue principle")

    assert result == {"type": "case_name", "normalized": "gladue principle", "original": "gladue principle"}


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
#  H3 — concept scaffold prefill follows the query
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def api_client():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_concept_scaffold_prefill_uses_user_query(api_client):
    """Scaffold disabled=False? No — SCAFFOLD_ENABLED on: the prefilled
    style_of_cause must be the user's own query, never a hardcoded example."""
    with patch("api.main.classify_and_normalize",
               return_value={"type": "concept", "normalized": "right to housing", "original": "right to housing"}), \
         patch("api.main.search_citation", return_value=[]), \
         patch("api.main.SCAFFOLD_ENABLED", True):
        resp = api_client.post("/api/citation", json={"input": "right to housing"})

    body = resp.json()
    assert body["status"] == "needs_input"
    assert body["data"]["prefill"]["style_of_cause"] == "right to housing"
    assert "duty to consult" not in body["data"]["prefill"]["style_of_cause"]


def test_concept_scaffold_unsupported_no_hardcoded_prefill_leak(api_client):
    with patch("api.main.classify_and_normalize",
               return_value={"type": "concept", "normalized": "right to housing", "original": "right to housing"}), \
         patch("api.main.search_citation", return_value=[]):
        resp = api_client.post("/api/citation", json={"input": "right to housing"})

    raw = resp.text
    assert "duty to consult" not in raw
