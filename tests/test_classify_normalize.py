"""Unit tests for classify_and_normalize() in citation_search.py,
plus supporting tests for call_gemini_text() logging and spend-tracker rates.

Run: pytest tests/test_classify_normalize.py -v
"""

import logging
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.citation_search import (
    classify_and_normalize,
    _infer_jurisdiction_canlii,
    expand_concept,
)
from core.spend_tracker import spend_tracker


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _make_gemini_response(text: str) -> str:
    """Simulate a raw Gemini response text (the parsed JSON as string)."""
    return text

_GEMINI_NONE_PATCH = patch("local_tools.citation_search.call_gemini_text", return_value=None)
_TIMING_PATCH = patch("profiling.timing.ENABLED", False)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  Bill fast-path (no LLM call at all, Gemini or DeepSeek)
# ═════════════════════════════════════════════════════════════════════════════

def test_bill_fastpath():
    """Bill C-22 -> regex fast-path, neither Gemini nor DeepSeek called."""
    mock_gemini = MagicMock()
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", mock_gemini), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = classify_and_normalize("Bill C-22")

    assert result["type"] == "bill"
    assert result["normalized"] == "C-22"
    mock_gemini.assert_not_called()
    mock_ds.assert_not_called()


def test_bill_fastpath_no_hyphen():
    """Bill c34 -> regex fast-path, normalised to C-34."""
    mock_gemini = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", mock_gemini):
        result = classify_and_normalize("bill c34")

    assert result["type"] == "bill"
    assert result["normalized"] == "C-34"
    mock_gemini.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 2.  Gemini primary path — expected success cases
# ═════════════════════════════════════════════════════════════════════════════

def test_ccc_expands_to_criminal_code():
    """"CCC" -> legislation, expanded to Criminal Code (regression: hallucinated under DeepSeek-no-thinking)."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Criminal Code, RSC 1985, c C-46", "original": "CCC"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("CCC")

    assert result["type"] == "legislation"
    assert "Criminal Code" in result["normalized"]
    assert "RSC" in result["normalized"]
    mock_ds.assert_not_called()


def test_charter_expands():
    """"Charter" -> legislation, expanded (regression: hallucinated incorrect expansion under DeepSeek-no-thinking)."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Canadian Charter of Rights and Freedoms", "original": "Charter"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("Charter")

    assert result["type"] == "legislation"
    assert "Charter" in result["normalized"]
    assert "Rights" in result["normalized"]
    mock_ds.assert_not_called()


def test_mixed_case_input_normalised():
    """"R. v. ShARM'a" -> case_name, consistent casing (R v, no dots)."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "case_name", "normalized": "R v Sharma", "original": "R. v. ShARM\'a"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("R. v. ShARM'a")

    assert result["type"] == "case_name"
    assert result["normalized"] == "R v Sharma"
    mock_ds.assert_not_called()


def test_citation_number_recognised():
    """"2022 SCC 39" -> citation_number (Gemini path)."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "citation_number", "normalized": "2022 SCC 39", "original": "2022 SCC 39"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("2022 SCC 39")

    assert result["type"] == "citation_number"
    mock_ds.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 3.  Gemini failure -> fallback to DeepSeek
# ═════════════════════════════════════════════════════════════════════════════

def test_gemini_returns_none_falls_back_to_deepseek():
    """Gemini returns None (network error) -> DeepSeek fallback executes."""
    fake_ds_response = _make_gemini_response(
        '{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'
    )
    with patch("local_tools.citation_search.call_gemini_text", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", return_value=fake_ds_response), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result["type"] == "case_name"
    assert result["normalized"] == "R v Gladue"


def test_gemini_non_json_response_falls_back():
    """Gemini returns non-JSON text -> DeepSeek fallback executes."""
    with patch("local_tools.citation_search.call_gemini_text", return_value="some plain text, not JSON"), \
         patch("local_tools.citation_search.ask_deepseek", return_value=(
             '{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'
         )), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result["type"] == "case_name"
    assert result["normalized"] == "R v Gladue"


def test_gemini_wrong_type_falls_back():
    """Gemini returns valid JSON but invalid type -> DeepSeek fallback executes."""
    with patch("local_tools.citation_search.call_gemini_text", return_value=(
        '{"type": "invalid_type", "normalized": "R v Gladue", "original": "R v Gladue"}'
    )), \
         patch("local_tools.citation_search.ask_deepseek", return_value=(
             '{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'
         )), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result["type"] == "case_name"


# ═════════════════════════════════════════════════════════════════════════════
# 4.  Both fail -> final fallback dict
# ═════════════════════════════════════════════════════════════════════════════

def test_both_llms_fail_returns_fallback():
    """Gemini fails AND DeepSeek raises -> fallback dict returned."""
    with patch("local_tools.citation_search.call_gemini_text", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", side_effect=RuntimeError("DS down")), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


# ═════════════════════════════════════════════════════════════════════════════
# 5.  Existing regression: DeepSeek error handling preserved (now via Gemini
#     failure + DeepSeek mock)
# ═════════════════════════════════════════════════════════════════════════════

def test_deepseek_raises_connection_error():
    """Gemini fails, then ask_deepseek raises ConnectionError -> fallback dict."""
    with patch("local_tools.citation_search.call_gemini_text", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", side_effect=ConnectionError("Connection refused")), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


def test_deepseek_raises_timeout():
    """Gemini fails, then ask_deepseek raises TimeoutError -> fallback dict."""
    with patch("local_tools.citation_search.call_gemini_text", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", side_effect=TimeoutError("timed out")), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


def test_deepseek_raises_generic_exception():
    """Gemini fails, then ask_deepseek raises generic Exception -> fallback dict."""
    with patch("local_tools.citation_search.call_gemini_text", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", side_effect=RuntimeError("API failure")), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result == {"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}


# ═════════════════════════════════════════════════════════════════════════════
# 6.  Spend tracker rate for gemini-2.5-flash
# ═════════════════════════════════════════════════════════════════════════════

def test_gemini_25_flash_rate_in_table():
    """gemini-2.5-flash has a dedicated rate entry (not using fallback)."""
    # _compute_cost is a pure method — no side effects, safe to call directly
    cost_input = spend_tracker._compute_cost("gemini", "gemini-2.5-flash", 1_000_000, 0)
    cost_output = spend_tracker._compute_cost("gemini", "gemini-2.5-flash", 0, 1_000_000)

    assert cost_input == 0.30, f"Expected $0.30 for 1M input tokens, got ${cost_input}"
    assert cost_output == 2.50, f"Expected $2.50 for 1M output tokens, got ${cost_output}"


def test_gemini_25_flash_rate_cheaper_than_fallback():
    """gemini-2.5-flash rate is lower than the unknown-model fallback."""
    # Before the rate was added, the fallback $0.8451 was used.
    # Now the dedicated rate is $0.30/$2.50, so an output-heavy call
    # should still be cheaper than using the fallback rate on input.
    cost_input = spend_tracker._compute_cost("gemini", "gemini-2.5-flash", 1_000_000, 0)
    from core.spend_tracker import _FALLBACK_RATE
    assert cost_input < _FALLBACK_RATE, (
        f"gemini-2.5-flash input rate ${cost_input} should be less than "
        f"fallback rate ${_FALLBACK_RATE}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 7.  Logging categories in call_gemini_text()
# ═════════════════════════════════════════════════════════════════════════════

def _make_http_resp(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a minimal mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.return_value = {}
    return resp


def test_call_gemini_timeout_logs_warning(caplog):
    """Timeout -> 'network/timeout' category warning logged, returns None."""
    caplog.set_level(logging.WARNING)

    with patch("llm_api.gemini_api.request_with_retry", side_effect=requests.exceptions.Timeout("timed out")), \
         _TIMING_PATCH:
        from llm_api.gemini_api import call_gemini_text
        result = call_gemini_text("test query")

    assert result is None
    assert any("network/timeout" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [network/timeout] for timeout"
    )


def test_call_gemini_non_200_logs_warning(caplog):
    """Non-200 HTTP status -> 'non-200' category warning logged, returns None."""
    caplog.set_level(logging.WARNING)

    mock_resp = _make_http_resp(status_code=429)
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=mock_resp
    )

    with patch("llm_api.gemini_api.request_with_retry", return_value=mock_resp), \
         _TIMING_PATCH:
        from llm_api.gemini_api import call_gemini_text
        result = call_gemini_text("test query")

    assert result is None
    assert any("non-200" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [non-200] for HTTP 429"
    )


def test_call_gemini_malformed_json_logs_warning(caplog):
    """Malformed JSON body -> 'json_parse' category warning logged, returns None."""
    caplog.set_level(logging.WARNING)

    mock_resp = _make_http_resp(status_code=200)
    mock_resp.json.side_effect = ValueError("Invalid JSON")

    with patch("llm_api.gemini_api.request_with_retry", return_value=mock_resp), \
         _TIMING_PATCH:
        from llm_api.gemini_api import call_gemini_text
        result = call_gemini_text("test query")

    assert result is None
    assert any("json_parse" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [json_parse] for malformed JSON"
    )


def test_call_gemini_bad_structure_logs_warning(caplog):
    """Valid JSON but missing expected response keys -> 'json_parse' warning, returns None."""
    caplog.set_level(logging.WARNING)

    # Valid JSON but missing "candidates" key
    mock_resp = _make_http_resp(status_code=200, json_data={"foo": "bar"})

    with patch("llm_api.gemini_api.request_with_retry", return_value=mock_resp), \
         _TIMING_PATCH:
        from llm_api.gemini_api import call_gemini_text
        result = call_gemini_text("test query")

    assert result is None
    assert any("json_parse" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [json_parse] for bad response structure"
    )


def test_gemini_timeout_logs_and_falls_back_to_deepseek(caplog):
    """Gemini timeout -> warning logged, DeepSeek fallback still executes."""
    caplog.set_level(logging.WARNING)

    with patch("llm_api.gemini_api.request_with_retry", side_effect=requests.exceptions.Timeout), \
         patch("local_tools.citation_search.ask_deepseek",
               return_value='{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result["type"] == "case_name"
    assert result["normalized"] == "R v Gladue"
    # Verify Gemini failure was logged
    assert any("network/timeout" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [network/timeout] on Gemini timeout, "
        "but fallback to DeepSeek still returned correct result"
    )


def test_gemini_non_200_logs_and_falls_back_to_deepseek(caplog):
    """Gemini HTTP 429 -> warning logged, DeepSeek fallback still executes."""
    caplog.set_level(logging.WARNING)

    mock_resp = _make_http_resp(status_code=429)
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=mock_resp
    )

    with patch("llm_api.gemini_api.request_with_retry", return_value=mock_resp), \
         patch("local_tools.citation_search.ask_deepseek",
               return_value='{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result["type"] == "case_name"
    assert any("non-200" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [non-200] for HTTP 429"
    )


def test_gemini_malformed_json_logs_and_falls_back_to_deepseek(caplog):
    """Gemini returns bad JSON -> warning logged, DeepSeek fallback still executes."""
    caplog.set_level(logging.WARNING)

    mock_resp = _make_http_resp(status_code=200)
    mock_resp.json.side_effect = ValueError("bad json")

    with patch("llm_api.gemini_api.request_with_retry", return_value=mock_resp), \
         patch("local_tools.citation_search.ask_deepseek",
               return_value='{"type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue"}'), \
         _TIMING_PATCH:
        result = classify_and_normalize("R v Gladue")

    assert result["type"] == "case_name"
    assert any("json_parse" in rec.getMessage() for rec in caplog.records), (
        "Expected a WARNING log with [json_parse] for malformed JSON"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 8.  Legislation abbreviation over-expansion guard
# ═════════════════════════════════════════════════════════════════════════════

def test_taxation_act_ontario_no_invented_citation():
    """"taxation act ontario" -> clean title, no invented citation number."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Taxation Act, Ontario", "original": "taxation act ontario"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("taxation act ontario")

    assert result["type"] == "legislation"
    # MUST NOT contain invented citation tokens like "SO", "c", "Sch", or year
    assert "SO" not in result["normalized"]
    assert "c " not in result["normalized"]
    assert "Sch" not in result["normalized"]
    assert "2007" not in result["normalized"]
    mock_ds.assert_not_called()


def test_ccc_abbreviation_still_expands():
    """"CCC" (known abbreviation) -> still expands to full citation (regression guard)."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Criminal Code, RSC 1985, c C-46", "original": "CCC"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("CCC")

    assert result["type"] == "legislation"
    assert "Criminal Code" in result["normalized"]
    assert "RSC" in result["normalized"]
    mock_ds.assert_not_called()


def test_ccc_with_pinpoint_still_expands():
    """"CCC s.718.2(e)" (known abbreviation + pinpoint) -> expands correctly."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Criminal Code, RSC 1985, c C-46, s 718.2(e)", '
        '"original": "CCC s.718.2(e)"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("CCC s.718.2(e)")

    assert result["type"] == "legislation"
    assert "Criminal Code" in result["normalized"]
    assert "RSC" in result["normalized"]
    assert "s 718.2(e)" in result["normalized"]
    mock_ds.assert_not_called()


def test_vancouver_charter_unaffected():
    """"Vancouver charter" -> legislation (clean title, no invented citation)."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Vancouver Charter", "original": "Vancouver charter"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("Vancouver charter")

    assert result["type"] == "legislation"
    assert "Vancouver" in result["normalized"]
    mock_ds.assert_not_called()


def test_family_law_act_ontario_clean_title():
    """"family law act ontario" -> clean title, no invented citation."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Family Law Act, Ontario", "original": "family law act ontario"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("family law act ontario")

    assert result["type"] == "legislation"
    # Check no invented citation tokens
    assert "RSO" not in result["normalized"]
    assert "SO " not in result["normalized"]
    assert "c " not in result["normalized"]
    mock_ds.assert_not_called()


def test_residential_tenancies_act_bc_clean_title():
    """"residential tenancies act bc" -> clean title, no invented citation."""
    fake_gemini_response = _make_gemini_response(
        '{"type": "legislation", "normalized": "Residential Tenancies Act, BC", '
        '"original": "residential tenancies act bc"}'
    )
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text", return_value=fake_gemini_response), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         _TIMING_PATCH:
        result = classify_and_normalize("residential tenancies act bc")

    assert result["type"] == "legislation"
    assert "RSBC" not in result["normalized"]
    assert "SBC" not in result["normalized"]
    assert "c " not in result["normalized"]
    mock_ds.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 9.  _infer_jurisdiction_canlii — Gemini primary, ambiguous-input fix,
#     DeepSeek fallback
# ═════════════════════════════════════════════════════════════════════════════

def test_infer_jurisdiction_no_signal_returns_none():
    """No jurisdiction signal -> Gemini returns 'unknown' -> function returns None."""
    mock_result = {"jurisdiction": "unknown"}
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=mock_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = _infer_jurisdiction_canlii("Animal Protection Act")

    assert result is None
    mock_ds.assert_not_called()


def test_infer_jurisdiction_federal_signal_returns_ca():
    """Federal signal ('Criminal Code') -> Gemini returns 'ca' -> function returns 'ca'."""
    mock_result = {"jurisdiction": "ca"}
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=mock_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = _infer_jurisdiction_canlii("Criminal Code")

    assert result == "ca"
    mock_ds.assert_not_called()


def test_infer_jurisdiction_ambiguous_returns_none():
    """Another ambiguous input -> Gemini returns 'unknown' -> None."""
    mock_result = {"jurisdiction": "unknown"}
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=mock_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = _infer_jurisdiction_canlii("Environmental Protection Act")

    assert result is None
    mock_ds.assert_not_called()


def test_infer_jurisdiction_ambiguous_returns_none_2():
    """Extra ambiguous input -> None."""
    mock_result = {"jurisdiction": "unknown"}
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=mock_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = _infer_jurisdiction_canlii("Health Protection Act")

    assert result is None
    mock_ds.assert_not_called()


def test_infer_jurisdiction_federal_case_mix():
    """"FEDERAL Environmental Act" (uppercase signal) -> Gemini returns 'ca' -> 'ca'."""
    mock_result = {"jurisdiction": "ca"}
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=mock_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = _infer_jurisdiction_canlii("FEDERAL Environmental Act")

    assert result == "ca"
    mock_ds.assert_not_called()


def test_infer_jurisdiction_province_signal_returns_code():
    """Ontario signal -> Gemini returns 'on' -> 'on'."""
    mock_result = {"jurisdiction": "on"}
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=mock_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        result = _infer_jurisdiction_canlii("Ontario Family Law Act")

    assert result == "on"
    mock_ds.assert_not_called()


def test_infer_jurisdiction_gemini_failure_falls_back_to_deepseek():
    """Gemini returns None (failure) -> DeepSeek fallback executes -> 'on'."""
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", return_value='{"jurisdiction": "on"}'):
        result = _infer_jurisdiction_canlii("Ontario Family Law Act")

    assert result == "on"


def test_infer_jurisdiction_gemini_out_of_enum_falls_back():
    """Gemini returns jurisdiction outside enum -> ignored -> DeepSeek fallback."""
    with patch("local_tools.citation_search.call_gemini_text_structured",
               return_value={"jurisdiction": "invalid_value"}), \
         patch("local_tools.citation_search.ask_deepseek", return_value='{"jurisdiction": "on"}'):
        result = _infer_jurisdiction_canlii("Ontario Family Law Act")

    assert result == "on"


def test_infer_jurisdiction_deepseek_unknown_returns_none():
    """Gemini fails, DeepSeek returns 'unknown' -> None."""
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", return_value='{"jurisdiction": "unknown"}'):
        result = _infer_jurisdiction_canlii("Animal Protection Act")

    assert result is None


def test_infer_jurisdiction_both_fail_returns_none():
    """Gemini fails AND DeepSeek raises -> None."""
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", side_effect=RuntimeError("DS down")):
        result = _infer_jurisdiction_canlii("Animal Protection Act")

    assert result is None


# ═════════════════════════════════════════════════════════════════════════════
# 10.  expand_concept — Gemini primary (thinking enabled, no A2AJ),
#      DeepSeek fallback (with A2AJ verification), double-failure ValueError
# ═════════════════════════════════════════════════════════════════════════════

def test_expand_concept_gladue_principle():
    """"gladue principle" -> Gemini returns Criminal Code, s718, R v Gladue, R v Ipeelee."""
    fake_gemini_result = {
        "candidates": [
            {"name": "Criminal Code, RSC 1985, c C-46, s 718.2(e)", "citation": None, "type": "legislation"},
            {"name": "R v Gladue", "citation": "[1999] 1 SCR 688", "type": "case"},
            {"name": "R v Ipeelee", "citation": "2012 SCC 13", "type": "case"},
        ]
    }
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=fake_gemini_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        results = expand_concept("gladue principle")

    assert len(results) >= 3
    names = [r.get("name", "") for r in results]
    citations = [r.get("neutral_citation") or "" for r in results]
    all_text = " ".join(names + citations)
    assert "Criminal Code" in all_text, f"Missing Criminal Code in {names}"
    assert "s 718" in all_text or "718.2" in all_text, f"Missing s718 in {names}"
    assert "R v Gladue" in all_text, f"Missing R v Gladue in {names}"
    assert "R v Ipeelee" in all_text, f"Missing R v Ipeelee in {names}"
    # Legislation candidate must include full pinpoint, not bare "Criminal Code"
    leg = [r for r in results if r.get("role") == "legislation"]
    if leg:
        assert "s 718.2(e)" in leg[0].get("name", ""), (
            f"Legislation candidate must include full section reference, got: {leg[0].get('name')}"
        )
    for r in results:
        assert r.get("verified") is False, f"Expected verified=False for Gemini-only path, got {r}"
        assert r.get("verification_source") == "llm_only", (
            f"Expected verification_source=llm_only, got {r.get('verification_source')}"
        )
    mock_ds.assert_not_called()


def test_expand_concept_oakes_test():
    """"oakes test" -> Gemini returns Charter, s 1, R v Oakes."""
    fake_gemini_result = {
        "candidates": [
            {"name": "Canadian Charter of Rights and Freedoms, s 1", "citation": None, "type": "legislation"},
            {"name": "R v Oakes", "citation": "[1986] 1 SCR 103", "type": "case"},
            {"name": "R v Big M Drug Mart", "citation": "[1985] 1 SCR 295", "type": "case"},
        ]
    }
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=fake_gemini_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        results = expand_concept("oakes test")

    assert len(results) >= 2
    names = [r.get("name", "") for r in results]
    citations = [r.get("neutral_citation") or "" for r in results]
    all_text = " ".join(names + citations)
    assert "Charter" in all_text, f"Missing Charter in {names}"
    assert "s 1" in all_text or "section 1" in all_text.lower(), f"Missing s 1 in {names}"
    assert "R v Oakes" in all_text, f"Missing R v Oakes in {names}"
    for r in results:
        assert r.get("verified") is False
        assert r.get("verification_source") == "llm_only"
    mock_ds.assert_not_called()


def test_expand_concept_reasonable_limits():
    """"reasonable limits" -> Gemini returns candidates."""
    fake_gemini_result = {
        "candidates": [
            {"name": "Canadian Charter of Rights and Freedoms, s 1", "citation": None, "type": "legislation"},
            {"name": "R v Oakes", "citation": "[1986] 1 SCR 103", "type": "case"},
        ]
    }
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=fake_gemini_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        results = expand_concept("reasonable limits")

    assert len(results) >= 1
    assert results[0].get("verified") is False
    assert results[0].get("verification_source") == "llm_only"
    mock_ds.assert_not_called()


def test_expand_concept_duty_of_care():
    """"duty of care" -> Gemini returns candidates."""
    fake_gemini_result = {
        "candidates": [
            {"name": "Donoghue v Stevenson", "citation": "[1932] AC 562", "type": "case"},
            {"name": "Anns v Merton London Borough Council", "citation": "[1978] AC 728", "type": "case"},
        ]
    }
    mock_ds = MagicMock()
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=fake_gemini_result), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds):
        results = expand_concept("duty of care")

    assert len(results) >= 1
    assert results[0].get("verified") is False
    assert results[0].get("verification_source") == "llm_only"
    mock_ds.assert_not_called()


def test_expand_concept_gemini_http_error_falls_back():
    """Gemini returns None (failure) -> DeepSeek fallback with A2AJ verification."""
    mock_ds = MagicMock(return_value=(
        '{"candidates": ['
        '{"name": "R v Gladue", "citation": "[1999] 1 SCR 688", "type": "case"}'
        "]}"
    ))
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         patch("local_tools.citation_search.fetch_by_citation"), \
         patch("local_tools.citation_search.search_cases_multi", return_value=[]):
        results = expand_concept("gladue principle")

    assert len(results) >= 1
    mock_ds.assert_called_once()


def test_expand_concept_gemini_timeout_falls_back():
    """Simulate Gemini timeout -> DeepSeek fallback."""
    mock_ds = MagicMock(return_value=(
        '{"candidates": ['
        '{"name": "R v Gladue", "citation": "[1999] 1 SCR 688", "type": "case"}'
        "]}"
    ))
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         patch("local_tools.citation_search.fetch_by_citation"), \
         patch("local_tools.citation_search.search_cases_multi", return_value=[]):
        results = expand_concept("gladue principle")

    assert len(results) >= 1
    mock_ds.assert_called_once()


def test_expand_concept_gemini_malformed_json_falls_back():
    """Gemini returns malformed JSON -> DeepSeek fallback."""
    mock_ds = MagicMock(return_value=(
        '{"candidates": ['
        '{"name": "R v Gladue", "citation": "[1999] 1 SCR 688", "type": "case"}'
        "]}"
    ))
    # Simulate a non-dict return (malformed)
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value={"foo": "bar"}), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         patch("local_tools.citation_search.fetch_by_citation"), \
         patch("local_tools.citation_search.search_cases_multi", return_value=[]):
        results = expand_concept("gladue principle")

    assert len(results) >= 1
    mock_ds.assert_called_once()


def test_expand_concept_gemini_empty_content_falls_back():
    """Gemini returns empty candidates list -> DeepSeek fallback."""
    mock_ds = MagicMock(return_value=(
        '{"candidates": ['
        '{"name": "R v Gladue", "citation": "[1999] 1 SCR 688", "type": "case"}'
        "]}"
    ))
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value={"candidates": []}), \
         patch("local_tools.citation_search.ask_deepseek", mock_ds), \
         patch("local_tools.citation_search.fetch_by_citation"), \
         patch("local_tools.citation_search.search_cases_multi", return_value=[]):
        results = expand_concept("gladue principle")

    assert len(results) >= 1
    mock_ds.assert_called_once()


def test_expand_concept_double_failure_raises_value_error():
    """Gemini fails AND DeepSeek fails -> ValueError raised."""
    with patch("local_tools.citation_search.call_gemini_text_structured", return_value=None), \
         patch("local_tools.citation_search.ask_deepseek", side_effect=RuntimeError("DS down")):
        with pytest.raises(ValueError, match="LLM expansion failed after fallback"):
            expand_concept("gladue principle")
