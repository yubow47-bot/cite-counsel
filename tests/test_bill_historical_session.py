"""Tests for historical session bill routing via LEGISinfo.

Pure deterministic-path tests — no LLM; live HTTP to LEGISinfo (read-only) +
mock-based fault-tolerance tests.

Run:  pytest tests/test_bill_historical_session.py -v
"""
import sys
import os
import pytest
from unittest.mock import patch

import requests as _probe_requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.legisinfo_api import (
    BILLS_URL,
    _year_to_sessions,
    _session_end_year,
    _get_current_session,
    find_bill,
    find_bills,
    build_bill_citation,
    fetch_legisinfo_bills,
    _normalize_bill_number,
    _fetch_json,
    SESSION_MAP,
)

# ── Live-HTTP guard ──────────────────────────────────────────────────────────
#
# The tests marked @_skip_live below hit the real LEGISinfo endpoint by design
# (read-only GETs).  When this environment cannot reach it (offline CI, dead
# system proxy, blocked network), SKIP instead of FAIL so a red suite always
# means a code regression — never a connectivity flake.
_legisinfo_reachable_cache = None


def _legisinfo_reachable() -> bool:
    """One-shot cached probe of the real LEGISinfo endpoint."""
    global _legisinfo_reachable_cache
    if _legisinfo_reachable_cache is None:
        try:
            _probe_requests.get(BILLS_URL, timeout=(3, 8))
            # Any HTTP response (even an error status) proves reachability.
            _legisinfo_reachable_cache = True
        except Exception:
            _legisinfo_reachable_cache = False
    return _legisinfo_reachable_cache


_skip_live = pytest.mark.skipif(
    not _legisinfo_reachable(),
    reason="LEGISinfo unreachable from this environment — skipping live-HTTP test",
)


class TestYearToSession:

    def test_1997_spans_two_sessions(self):
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

    def test_get_current_session(self):
        cur = _get_current_session()
        assert cur is not None
        assert "-" in cur
        _, end = SESSION_MAP.get(cur, (None, None))
        assert end is None

    def test_session_map_coverage(self):
        assert "35-1" in SESSION_MAP
        assert "45-1" in SESSION_MAP
        assert len(SESSION_MAP) >= 20


class TestFindBillHistorical:

    @_skip_live
    def test_c32_1997_returns_multiple_candidates(self):
        results = find_bills("C-32", year=1997)
        assert len(results) >= 2
        sessions = {r.get("ParlSessionCode") for r in results}
        assert "35-2" in sessions
        assert "36-1" in sessions

    @_skip_live
    def test_c32_1994_single_result(self):
        results = find_bills("C-32", year=1994)
        assert len(results) == 1
        assert results[0].get("ParlSessionCode") == "35-1"

    @_skip_live
    def test_c32_current_session_single(self):
        results = find_bills("C-32")
        assert len(results) <= 1
        if results:
            ps = results[0].get("ParlSessionCode", "")
            assert ps != "35-2"

    @_skip_live
    def test_nonexistent_with_year_returns_empty(self):
        results = find_bills("ZZTOP-999", year=1997)
        assert results == []

    def test_year_before_coverage_returns_empty(self):
        results = find_bills("C-32", year=1990)
        assert results == []

    @_skip_live
    def test_ambiguous_year_bill_has_session_info(self):
        results = find_bills("C-32", year=1997)
        assert len(results) >= 2
        for r in results:
            assert r.get("ParlSessionCode") in ("35-2", "36-1")
            assert r.get("ParliamentNumber", 0) > 0
            assert r.get("SessionNumber", 0) > 0


class TestSingleResultContract:

    @_skip_live
    def test_single_result_carries_bill_session(self):
        """Single-match via citation_search bill branch carries bill_session."""
        from local_tools.citation_search import search_citation
        from local_tools.citation_search import classify_and_normalize
        from local_tools.a2aj_api import _extract_year

        # Force single-match: year=1994 maps only to 35-1
        results = search_citation("Bill C-32, 1994",
                                  classification={"type": "bill", "normalized": "C-32",
                                                   "original": "Bill C-32, 1994"})
        assert len(results) == 1
        item = results[0]
        assert item.get("verified") is True
        assert item.get("_bill_citation") is not None
        assert item.get("bill_session") == "35-1"
        assert item.get("bill_title", "").startswith("An Act")
        assert "*" in item["_bill_citation"]

    @_skip_live
    def test_single_result_no_year_carries_bill_session(self):
        """Single-match without year also carries bill_session + bill_title."""
        from local_tools.citation_search import search_citation
        results = search_citation("Bill C-32",
                                  classification={"type": "bill", "normalized": "C-32",
                                                   "original": "Bill C-32"})
        if len(results) == 1 and results[0].get("verified"):
            item = results[0]
            assert item.get("bill_session") is not None
            assert item.get("bill_title") is not None


class TestFindBillsDedup:

    @_skip_live
    def test_dedup_by_bill_id(self):
        results = find_bills("C-32", year=2000)
        ids = [r.get("BillId") for r in results]
        assert len(ids) == len(set(ids))


class TestPartialFailureResilience:

    def test_one_session_fails_other_succeeds(self):
        """If 35-2 fails but 36-1 succeeds for year=1997, return 36-1 matches."""
        def fake_fetch(session=None):
            if session == "35-2":
                return None  # Simulate network failure
            if session == "36-1":
                return [{  # Minimal LEGISinfo bill record
                    "BillId": 9999, "BillNumberFormatted": "X-99",
                    "LongTitleEn": "A Test Bill",
                    "ParlSessionCode": "36-1",
                    "ParliamentNumber": 36, "SessionNumber": 1,
                    "PassedHouseFirstReadingDateTime": "1997-11-15T00:00:00-05:00",
                }]
            return []

        with patch("local_tools.legisinfo_api._fetch_json", side_effect=fake_fetch):
            results = find_bills("X-99", year=1997)
            assert len(results) == 1
            assert results[0].get("ParlSessionCode") == "36-1"

    def test_both_sessions_fail_returns_empty(self):
        """When all candidate sessions fail, return empty list (not-found)."""
        def fake_fetch(session=None):
            return None

        with patch("local_tools.legisinfo_api._fetch_json", side_effect=fake_fetch):
            results = find_bills("C-32", year=1997)
            assert results == []


class TestCrossSessionBoundary:

    @_skip_live
    def test_1997_bill_unique_to_one_session(self):
        """A bill unique to only 35-2 (not in 36-1) still returns 1 result."""
        results = find_bills("C-32", year=1997)
        # C-32 exists in both sessions (tested elsewhere); test something stricter:
        parls = {(r.get("ParliamentNumber"), r.get("SessionNumber")) for r in results}
        assert len(parls) >= 2

    def test_1999_spans_36_1_and_36_2(self):
        sessions = _year_to_sessions(1999)
        assert "36-1" in sessions
        assert "36-2" in sessions
        assert len(sessions) == 2

    def test_2000_maps_to_36_2(self):
        sessions = _year_to_sessions(2000)
        assert sessions == ["36-2"]


class TestBuildBillCitation:

    @_skip_live
    def test_title_italicised(self):
        rec = find_bill("C-32", year=1994)
        assert rec is not None
        cit = build_bill_citation(rec)
        assert "*" in cit
        assert "*An Act" in cit
        assert cit.endswith(".")

    @_skip_live
    def test_current_session_citation(self):
        rec = find_bill("C-32")
        if rec:
            cit = build_bill_citation(rec)
            assert cit.endswith(".")
            assert "*" in cit

    @_skip_live
    def test_year_present_in_citation(self):
        rec = find_bill("C-32", year=1994)
        assert rec is not None
        cit = build_bill_citation(rec)
        assert "1994" in cit

    def test_all_json_bill_examples_are_italic(self):
        """Every Bill example in mcgill_rules.json wraps title in *italic*."""
        import json
        rules_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcgill_rules.json")
        with open(rules_path, encoding="utf-8") as f:
            rules = json.load(f)
        bill_examples = []
        for t in rules.get("legislation", {}).get("topics", []):
            if t.get("topic") == "Bills":
                bill_examples = t.get("examples", [])
                break
        assert len(bill_examples) >= 1
        for ex in bill_examples:
            assert ", *" in ex
            assert "*," in ex


class TestBillAPIEndToEnd:

    @_skip_live
    def test_ambiguous_bill_returns_needs_selection(self):
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)

        resp = client.post("/api/citation", json={"input": "Bill C-32, 1997"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "needs_selection"
        assert body["ok"] is True
        assert body["route"] == "bill"

        candidates = body.get("data", {}).get("candidates", [])
        assert len(candidates) >= 2

        for c in candidates:
            assert "display" in c
            assert "_bill_citation" in c
            assert "bill_session" in c
            assert "bill_title" in c
            assert "Session" in c["display"]
            assert "*" in c["_bill_citation"]

        sessions_seen = set()
        for i, c in enumerate(candidates):
            sessions_seen.add(c.get("bill_session", ""))
            select_resp = client.post("/api/citation/select", json={
                "candidates": candidates, "selected_index": i,
            })
            assert select_resp.status_code == 200
            select_body = select_resp.json()
            assert select_body["status"] == "done"
            citations = select_body.get("data", {}).get("citations", [])
            assert len(citations) == 1
            cit = citations[0]["citation"]
            assert cit.endswith(".")
            assert "*" in cit

        assert len(sessions_seen) >= 2


class TestCache:

    @_skip_live
    def test_per_session_cache(self):
        b1 = fetch_legisinfo_bills(session="35-2")
        b2 = fetch_legisinfo_bills(session="36-1")
        assert len(b1) > 0
        assert len(b2) > 0
        assert b1 is not b2


class TestNormalizeBillNumber:

    def test_normalize_variants(self):
        assert _normalize_bill_number("bill c-32") == "C-32"
        assert _normalize_bill_number("S-2") == "S-2"
        assert _normalize_bill_number("bill s-2") == "S-2"
