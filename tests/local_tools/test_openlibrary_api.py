"""Tests for openlibrary_api deterministic builder and _wrap_italic helper."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.openlibrary_api import _wrap_italic, build_book_citation


class TestWrapItalic:

    def test_plain_string_wrapped(self):
        assert _wrap_italic("Siddhartha") == "*Siddhartha*"

    def test_already_wrapped_noop(self):
        assert _wrap_italic("*Siddhartha*") == "*Siddhartha*"

    def test_internal_asterisks_untouched(self):
        result = _wrap_italic("text *inner* text")
        assert result == "*text *inner* text*"

    def test_empty_string(self):
        assert _wrap_italic("") == ""

    def test_none_returns_none(self):
        assert _wrap_italic(None) is None


class TestBuildBookCitation:

    def test_italicizes_title(self):
        ol_data = {
            "authors": [{"name": "Hermann Hesse"}],
            "title": "Siddhartha",
            "subtitle": "an Indian tale",
            "publishers": [{"name": "Brandywine Studio Press"}],
            "publish_places": [{"name": "United States"}],
            "publish_date": "2008",
        }
        cit = build_book_citation(ol_data)
        assert cit is not None
        assert "*Siddhartha: an Indian tale*" in cit

    def test_does_not_double_wrap(self):
        ol_data = {
            "authors": [{"name": "Author"}],
            "title": "*Pre Wrapped*",
            "publishers": [{"name": "Pub"}],
            "publish_date": "2020",
        }
        cit = build_book_citation(ol_data)
        assert cit is not None
        assert "**" not in cit
        assert "*Pre Wrapped*" in cit

    def test_missing_fields_returns_none(self):
        assert build_book_citation({}) is None
