"""Tests for prompt hygiene (Commit A).

Covers:
- build_prompt serialization-time field filter: bulk/internal fields never
  reach the prompt, and the INPUT fields dict is never mutated (raw_text has
  real consumers upstream: DOI path, ISBN path, select_subpattern gov branch)
- date humanization: ISO -> McGill prose dates, idempotent, garbage-safe
- asterisk balance guard: balanced/no-op, repair-at-boundary, strip-fallback

Run: pytest tests/test_prompt_hygiene.py -v
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mcgill_engine import (
    build_prompt,
    format_citation,
    get_last_debug,
    _ensure_balanced_asterisks,
    _humanize_date,
    _prompt_fields,
    NotADoiError,
    InvalidIsbnError,
)

RULES = {"category": "Jurisprudence", "topics": []}


# ═════════════════════════════════════════════════════════════════════════════
#  Field filter
# ═════════════════════════════════════════════════════════════════════════════

def test_prompt_excludes_bulk_and_internal_fields():
    fields = {
        "style_of_cause": "R v Gladue",
        "neutral_citation": "1999 CanLII 679",
        "reporter": "[1999] 1 SCR 688",
        "raw_text": "FULL" + "x" * 4000,       # must never reach the prompt
        "hostname": "cigionline.org",
        "verified": True,
        "warning": "⚠️ 未能通过验证",
        "display": "✅ R v Gladue — 1999 CanLII 679",
        "_bill_citation": "internal",
    }
    prompt = build_prompt(fields, "jurisprudence", RULES)

    assert "FULL" not in prompt
    assert "cigionline.org" not in prompt
    assert "verified" not in prompt
    assert "未能通过验证" not in prompt
    assert "CanLII 679 —" not in prompt          # display label fragment
    assert "internal" not in prompt
    assert "R v Gladue" in prompt                 # real field still present


def test_prompt_filter_never_mutates_input_dict():
    """The raw_text consumers (DOI/ISBN paths, select_subpattern gov branch)
    read the ORIGINAL dict before build_prompt — filtering must not mutate it."""
    fields = {
        "raw_text": "10.1234/abc",
        "hostname": "example.com",
        "verified": True,
        "author": "Jane Smith",
        "empty": "",
        "nul": None,
    }
    snapshot = dict(fields)
    _prompt_fields(fields)
    build_prompt(fields, "jurisprudence", RULES)
    assert fields == snapshot, "build_prompt/_prompt_fields must not mutate fields"


def test_prompt_drops_none_and_blank_but_keeps_values():
    out = _prompt_fields({"a": None, "b": "", "c": "   ", "d": "x"})
    assert out == {"d": "x"}


# ═════════════════════════════════════════════════════════════════════════════
#  Date humanization
# ═════════════════════════════════════════════════════════════════════════════

def test_humanize_full_iso_date():
    assert _humanize_date("2017-04-25") == "25 April 2017"
    assert _humanize_date("1999-01-03") == "3 January 1999"


def test_humanize_year_month():
    assert _humanize_date("2017-04") == "April 2017"


def test_humanize_bare_year_and_prose_unchanged():
    assert _humanize_date("2017") == "2017"
    assert _humanize_date("25 April 2017") == "25 April 2017"   # no double conversion
    assert _humanize_date("last modified 14 April 2019") == "last modified 14 April 2019"
    assert _humanize_date("not-a-date") == "not-a-date"
    assert _humanize_date(None) is None
    assert _humanize_date(20230425) == 20230425


def test_humanize_invalid_month_day_passes_through():
    assert _humanize_date("2017-13-40") == "2017-13-40"          # garbage stays verbatim


def test_prompt_humanizes_date_field():
    fields = {"author": "A", "title": "T", "date": "2017-04-25", "website": "example.com"}
    prompt = build_prompt(fields, "secondary_sources.websites", RULES)
    assert "25 April 2017" in prompt
    assert "2017-04-25" not in prompt


# ═════════════════════════════════════════════════════════════════════════════
#  Asterisk balance guard
# ═════════════════════════════════════════════════════════════════════════════

def test_guard_balanced_unchanged():
    cit = "*R v Sharma*, 2022 SCC 39, [2022] 3 SCR 147."
    assert _ensure_balanced_asterisks(cit) == cit


def test_guard_no_asterisks_unchanged():
    assert _ensure_balanced_asterisks("SOR/2000-111.") == "SOR/2000-111."


def test_guard_repairs_at_first_comma_boundary():
    # the observed glm failure: opening '*' with the closing one dropped
    cit = "*R v Sharma, 2022 SCC 39, [2022] 3 SCR 147."
    got = _ensure_balanced_asterisks(cit)
    assert got == "*R v Sharma*, 2022 SCC 39, [2022] 3 SCR 147."


def test_guard_strips_when_no_boundary():
    cit = "R v Sharma* 2022"
    got = _ensure_balanced_asterisks(cit)
    assert "*" not in got
    assert got == "R v Sharma 2022"


def test_guard_records_intervention_in_debug():
    cit = "*R v Sharma, 2022 SCC 39."
    _ensure_balanced_asterisks(cit)
    assert get_last_debug()["asterisk_guard"] is not None
    assert "repaired" in get_last_debug()["asterisk_guard"]


# ═════════════════════════════════════════════════════════════════════════════
#  Deterministic paths still see raw_text (the reason filtering must be
#  serialization-time only)
# ═════════════════════════════════════════════════════════════════════════════

def test_doi_path_still_reads_raw_text():
    """DOI extraction consumes fields['raw_text'] BEFORE build_prompt — the
    filter must not have broken it."""
    with patch("core.mcgill_engine.ask_deepseek") as mock_ds:
        try:
            format_citation({"raw_text": "definitely not a doi"}, doc_type="journal_article")
            raised = None
        except NotADoiError as e:
            raised = e
    assert isinstance(raised, NotADoiError)
    mock_ds.assert_not_called()          # deterministic failure, no LLM spend


def test_isbn_path_still_reads_raw_text():
    with patch("core.mcgill_engine.ask_deepseek") as mock_ds:
        try:
            format_citation({"raw_text": "9780132350885"}, doc_type="book")
            raised = None
        except InvalidIsbnError as e:
            raised = e                   # 9780132350885 fails checksum by design
        except Exception as e:
            raised = e
    assert isinstance(raised, InvalidIsbnError)
    mock_ds.assert_not_called()
