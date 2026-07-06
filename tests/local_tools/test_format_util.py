"""Tests for format_util._wrap_italic — pure string transformation, no I/O."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.format_util import _wrap_italic
from local_tools.utils import extract_pinpoint, extract_case_pinpoint


class TestWrapItalic:
    """_wrap_italic: deterministic string formatting, no LLM or network."""

    def test_plain_string_wrapped(self):
        assert _wrap_italic("hello") == "*hello*"

    def test_already_wrapped_noop(self):
        assert _wrap_italic("*hello*") == "*hello*"

    def test_whitespace_stripped_before_wrap(self):
        assert _wrap_italic("  hello  ") == "*hello*"

    def test_none_returns_none(self):
        assert _wrap_italic(None) is None

    def test_empty_returns_empty(self):
        assert _wrap_italic("") == ""

    def test_internal_asterisk_preserved(self):
        assert _wrap_italic("a*b") == "*a*b*"


def test_shared_singleton():
    """Both modules import the SAME _wrap_italic from format_util."""
    from local_tools.legisinfo_api import _wrap_italic as wi_bill
    from local_tools.openlibrary_api import _wrap_italic as wi_book
    from local_tools.format_util import _wrap_italic as wi_src
    assert wi_bill is wi_src
    assert wi_book is wi_src


# ═══════════════════════════════════════════════════════════════════
#  extract_pinpoint — shared helper for pinpoint extraction
# ═══════════════════════════════════════════════════════════════════

class TestExtractPinpoint:
    """extract_pinpoint: deterministic string parsing, no I/O."""

    def test_pinpoint_present(self):
        assert extract_pinpoint("Criminal Code, RSC 1985, c C-46, s 718.2(e)") == "s 718.2(e)"

    def test_no_pinpoint(self):
        assert extract_pinpoint("Criminal Code, RSC 1985, c C-46") == ""

    def test_act_level_only(self):
        assert extract_pinpoint("RSC 1985, c C-46") == ""

    def test_sc_regulation(self):
        assert extract_pinpoint("Youth Criminal Justice Act, SC 2002, c 1, s 3(1)(a)(ii)") == "s 3(1)(a)(ii)"

    def test_sor_regulation(self):
        # CRC is not in the citation regex; returns "" (known boundary, not broadened)
        assert extract_pinpoint("Migratory Birds Regulations, CRC, c 1035, s 4") == ""

    def test_bc_regulation(self):
        assert extract_pinpoint("Some Act, BC Reg 123/2020, s 7(2)") == "s 7(2)"

    def test_empty_string(self):
        assert extract_pinpoint("") == ""

    def test_no_citation_match(self):
        assert extract_pinpoint("Some random text without a citation") == ""


# ═══════════════════════════════════════════════════════════════════
#  extract_case_pinpoint — new helper for case-route pinpoint
# ═══════════════════════════════════════════════════════════════════

class TestExtractCasePinpoint:
    """extract_case_pinpoint: deterministic string parsing, no I/O."""

    def test_at_para_n(self):
        assert extract_case_pinpoint("r v leo at para 2") == "at para 2"

    def test_at_paras_range(self):
        assert extract_case_pinpoint("r v sharma at paras 10-15") == "at paras 10-15"

    def test_bare_at_number(self):
        assert extract_case_pinpoint("r v smith at 47") == "at 47"

    def test_at_p_n(self):
        assert extract_case_pinpoint("r v jones at p 5") == "at p 5"

    def test_at_pp_range(self):
        assert extract_case_pinpoint("r v brown at pp 10-15") == "at pp 10-15"

    def test_no_pinpoint_no_match(self):
        assert extract_case_pinpoint("R v Oakes") == ""

    def test_trailing_period_no_match(self):
        assert extract_case_pinpoint("R v Oakes.") == ""

    def test_citation_number_no_match(self):
        assert extract_case_pinpoint("2022 SCC 39") == ""

    def test_reporter_no_match(self):
        assert extract_case_pinpoint("1986 1 scr 103") == ""

    def test_pinpoint_not_at_end_no_match(self):
        assert extract_case_pinpoint("at para 2 something else") == ""

    def test_empty_string(self):
        assert extract_case_pinpoint("") == ""

    def test_none_input(self):
        assert extract_case_pinpoint(None) == ""

    def test_at_para_single_digit(self):
        assert extract_case_pinpoint("r v wong at para 5") == "at para 5"

    def test_case_insensitive(self):
        assert extract_case_pinpoint("R v PATEL AT PARA 42") == "AT PARA 42"

    # ── New tests for Item 2 (extract_case_pinpoint boundary gap) ─────────

    def test_paras_plural_single_number(self):
        """'at paras N' (plural, single number, no range) is now matched."""
        assert extract_case_pinpoint("r v smith at paras 10") == "at paras 10"

    def test_no_boundary_false_positive(self):
        """Word-boundary guard on 'at' prevents match inside a longer token."""
        assert extract_case_pinpoint("R v Format10") == ""

    def test_existing_paras_range_still_works(self):
        """Regression guard: 'at paras N-M' still matches the whole range."""
        assert extract_case_pinpoint("r v jones at paras 10-15") == "at paras 10-15"
