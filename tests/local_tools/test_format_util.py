"""Tests for format_util._wrap_italic — pure string transformation, no I/O."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.format_util import _wrap_italic
from local_tools.utils import extract_pinpoint


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
