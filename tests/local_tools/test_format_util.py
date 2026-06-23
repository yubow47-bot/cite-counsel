"""Tests for format_util._wrap_italic — pure string transformation, no I/O."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.format_util import _wrap_italic


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
