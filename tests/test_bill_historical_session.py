"""Tests for historical session bill routing via LEGISinfo.

Pure deterministic-path tests — no LLM, some live HTTP to LEGISinfo.
All calls are read-only (LEGISinfo JSON is public, no auth required).

Run:  pytest tests/test_bill_historical_session.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.legisinfo_api import (
    _year_to_sessions,
    _session_end_year,
    find_bill,
    build_bill_citation,
    fetch_legisinfo_bills,
    _normalize_bill_number,
    SESSION_MAP,
)


class TestYearToSession:

    def test_1997_spans_two_sessions(self):
        """Year 1997 covers both 36-1 (Sep) and 35-2 (Feb-Apr)."""
        sessions = _year_to_sessions(1997)
        assert "35-2" in sessions
        assert "36-1" in sessions
        assert len(sessions) == 2

    def test_1994_only_35_1(self):
        sessions = _year_to_sessions(1994)
        assert sessions == ["35-1"]

    def test_2000_only_36_2(self):
        sessions = _year_to_sessions(2000)
        assert "36-2" in sessions
        assert len(sessions) == 1

    def test_2025_spans_44_1_and_45_1(self):
        sessions = _year_to_sessions(2025)
        assert "44-1" in sessions
        assert "45-1" in sessions

    def test_2026_current_session_only(self):
        sessions = _year_to_sessions(2026)
        assert sessions == ["45-1"]

    def test_1990_before_coverage_empty(self):
        sessions = _year_to_sessions(1990)
        assert sessions == []

    def test_session_end_year(self):
        assert _session_end_year("35-2") == 1997
        assert _session_end_year("36-1") == 1999
        assert _session_end_year("44-1") == 2025
        assert _session_end_year("45-1") is None

    def test_session_map_coverage(self):
        """SESSION_MAP covers at least 35-1 (1994) through current."""
        assert "35-1" in SESSION_MAP
        assert "45-1" in SESSION_MAP
        assert len(SESSION_MAP) >= 20


class TestFindBillHistorical:

    def test_c32_1997_finds_historical(self):
        """Bill C-32, 1997 should find either 35-2 or 36-1."""
        rec = find_bill("C-32", year=1997)
        assert rec is not None
        ps = rec.get("ParlSessionCode", "")
        assert ps in ("35-2", "36-1"), f"Unexpected session: {ps}"

    def test_c32_1994_finds_35_1(self):
        """Bill C-32, 1994 should find 35-1."""
        rec = find_bill("C-32", year=1994)
        assert rec is not None
        assert rec.get("ParlSessionCode") == "35-1"

    def test_c32_current_session(self):
        """Bill C-32 (no year) — current session only."""
        rec = find_bill("C-32")
        if rec:
            ps = rec.get("ParlSessionCode", "")
            assert ps != "35-2", "Should not return historical session for no-year lookup"

    def test_nonexistent_with_year_returns_none(self):
        """Non-existent bill with year must not fall back to current session."""
        rec = find_bill("ZZTOP-999", year=1997)
        assert rec is None

    def test_year_before_coverage_returns_none(self):
        rec = find_bill("C-32", year=1990)
        assert rec is None


class TestBuildBillCitation:

    def test_title_italicised(self):
        """build_bill_citation wraps title in *italic* markers."""
        rec = find_bill("C-32", year=1994)
        assert rec is not None
        cit = build_bill_citation(rec)
        assert "*" in cit, "Title should have italic markers"
        assert "*An Act" in cit, "Title should start with italicised An Act"
        assert cit.endswith("."), "Citation must end with period"

    def test_current_session_citation(self):
        """Current session citation still works (no year = current)."""
        rec = find_bill("C-32")
        if rec:
            cit = build_bill_citation(rec)
            assert cit.endswith(".")
            assert "*" in cit, "Title should be italicised"

    def test_year_present_in_citation(self):
        """Citation contains the year (from date field or session fallback)."""
        rec = find_bill("C-32", year=1994)
        assert rec is not None
        cit = build_bill_citation(rec)
        assert "1994" in cit, f"Year 1994 missing from: {cit}"


class TestCache:

    def test_per_session_cache(self):
        """Different sessions have separate caches."""
        b1 = fetch_legisinfo_bills(session="35-2")
        b2 = fetch_legisinfo_bills(session="36-1")
        assert len(b1) > 0
        assert len(b2) > 0
        # Different sessions = different bill counts typically
        assert b1 is not b2  # Different list objects


class TestNormalizeBillNumber:

    def test_normalize_variants(self):
        assert _normalize_bill_number("bill c-32") == "C-32"
        assert _normalize_bill_number("S-2") == "S-2"
        assert _normalize_bill_number("bill s-2") == "S-2"
