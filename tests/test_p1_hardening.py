"""Tests for the P1 security/robustness hardening batch.

Covers:
- /api/citation/select: internal keys stripped, _bill_citation re-derived
  server-side (fabricated text never echoed), degrade when un-confirmable
- Pydantic payload caps (422 on oversized bodies)
- rate limiter: bounded IP table + env robustness
- Discord: allowed_mentions + fence/mention sanitization
- spend_tracker: HF flush happens outside the lock
- legisinfo find_bills: uses the session cache, keeps id-less matches
- mcgill_engine: typed DOI/ISBN sentinel errors

Run: pytest tests/test_p1_hardening.py -v
"""

import os
import sys
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from api.main import app, _sign_candidate
from api.rate_limiter import RateLimiter, _int_env

client = TestClient(app)

_TIMING_PATCH = patch("profiling.timing.ENABLED", False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """The suite shares one in-process per-IP limiter bucket."""
    with patch("api.main.rate_limiter.check", return_value=True):
        yield


# ═════════════════════════════════════════════════════════════════════════════
#  /api/citation/select — grounding hardening
# ═════════════════════════════════════════════════════════════════════════════

def test_select_fabricated_bill_citation_is_ignored():
    """A tampered _bill_citation must never be echoed — the citation is
    rebuilt from LEGISinfo (mocked here to confirm the record)."""
    fake_bill = {
        "BillNumberFormatted": "C-22",
        "LongTitleEn": "An Act respecting real measures",
        "ShortTitleEn": "",
        "ParliamentNumber": 44,
        "SessionNumber": 1,
        "BillId": 12345,
        "ParlSessionCode": "44-1",
    }
    with patch("local_tools.legisinfo_api.fetch_legisinfo_bills",
               MagicMock(return_value=[fake_bill])), \
         patch("api.main.classify_document_type") as mock_cls:
        candidate = _sign_candidate({
                "verified": True,
                "style_of_cause": "Bill C-22",
                "bill_session": "44-1",
                "bill_title": "An Act respecting real measures",
                "_bill_citation": "EVIL INJECTED CITATION TEXT",
            })
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })

    body = resp.json()
    assert body["status"] == "done"
    citation = body["data"]["citations"][0]["citation"]
    assert "EVIL" not in citation
    assert "C-22" in citation
    mock_cls.assert_not_called()  # deterministic path, no LLM


def test_select_bill_not_reconfirmable_degrades():
    """bill_session candidate that LEGISinfo can't confirm → unsupported."""
    with patch("local_tools.legisinfo_api.fetch_legisinfo_bills", MagicMock(return_value=[])):
        candidate = _sign_candidate({
                "verified": True,
                "style_of_cause": "Bill X-99",
                "bill_session": "44-1",
            })
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })
    body = resp.json()
    assert body["status"] == "unsupported"


def test_select_strips_all_internal_keys():
    """Non-bill candidate: every underscore-prefixed key is stripped before
    formatting — format_citation never sees client internal fields."""
    with patch("api.main.format_citation", MagicMock(return_value="Citation.")) as mock_fmt:
        candidate = _sign_candidate({
                "verified": True,
                "style_of_cause": "R v Legit",
                "neutral_citation": "2025 SCC 7",
                "_bill_citation": "should be dropped",
                "_match_path": "smuggled",
                "_anything": "gone",
            })
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })
    assert resp.json()["status"] == "done"
    passed = mock_fmt.call_args[0][0]
    assert not any(k.startswith("_") for k in passed)


def test_candidate_payloads_ship_no_internal_keys():
    """Server → client candidate lists never include underscore keys."""
    with patch("api.main.classify_and_normalize",
               return_value={"type": "case_name", "normalized": "R v Multi", "original": "R v Multi"}), \
         patch("api.main.search_citation", return_value=[
             {"style_of_cause": "R v A", "verified": True, "_bill_citation": "x", "_match_path": "y"},
             {"style_of_cause": "R v B", "verified": True},
         ]):
        resp = client.post("/api/citation", json={"input": "R v Multi"})

    body = resp.json()
    assert body["status"] == "needs_selection"
    for cand in body["data"]["candidates"]:
        assert not any(k.startswith("_") for k in cand)


# ═════════════════════════════════════════════════════════════════════════════
#  Pydantic payload caps
# ═════════════════════════════════════════════════════════════════════════════

def test_oversized_input_rejected():
    resp = client.post("/api/citation", json={"input": "x" * 2500})
    assert resp.status_code == 422


def test_too_many_candidates_rejected():
    resp = client.post("/api/citation/select", json={
        "candidates": [{"verified": True}] * 21,
        "selected_index": 0,
    })
    assert resp.status_code == 422


def test_too_many_chat_messages_rejected():
    msgs = [{"role": "user", "content": "hi"}] * 21
    resp = client.post("/api/chat", json={"messages": msgs})
    assert resp.status_code == 422


def test_too_many_assembly_fields_rejected():
    resp = client.post("/api/citation/assemble", json={
        "type": "book",
        "fields": {f"field{i}": "v" for i in range(61)},
    })
    assert resp.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
#  Rate limiter — bounded table + env robustness
# ═════════════════════════════════════════════════════════════════════════════

def test_rate_limiter_sweeps_stale_buckets_when_capped():
    limiter = RateLimiter()
    stale = 1.0  # ancient timestamp (well outside both windows)
    for i in range(8):
        limiter._buckets[f"10.0.0.{i}"] = [stale]
    with patch("api.rate_limiter._MAX_TRACKED_IPS", 5):
        assert limiter.check("fresh-ip") is True
    assert len(limiter._buckets) <= 6  # stale entries evicted, fresh kept
    assert "10.0.0.0" not in limiter._buckets


def test_rate_limiter_env_garbage_falls_back_to_default():
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "not-a-number"}):
        assert _int_env("RATE_LIMIT_PER_MIN", 30) == 30
    with patch.dict(os.environ, {"RATE_LIMIT_PER_HOUR": "0"}):
        assert _int_env("RATE_LIMIT_PER_HOUR", 200) == 200


# ═════════════════════════════════════════════════════════════════════════════
#  Discord notification hardening
# ═════════════════════════════════════════════════════════════════════════════

def test_discord_sanitizes_and_disables_mentions():
    from core import discord_notify
    post = MagicMock()
    post.return_value.raise_for_status = MagicMock()
    with patch.dict(os.environ, {"DISCORD_FEEDBACK_WEBHOOK": "https://discord/hook"}), \
         patch.object(discord_notify.discord_session, "post", post):
        ok = discord_notify.notify({
            "kind": "rating", "verdict": "up",
            "output": "```injected fence``` @everyone look",
            "note": "ping @here please",
        })
    assert ok is True
    payload = post.call_args.kwargs["json"]
    assert payload["allowed_mentions"] == {"parse": []}
    # Only the template's own fences may remain; the user's fence attempt is
    # neutralized to ''' so it cannot break out of the code block.
    assert payload["content"].count("```") == 2
    assert "'''injected fence'''" in payload["content"]
    assert "@everyone" not in payload["content"]
    assert "@here" not in payload["content"]


# ═════════════════════════════════════════════════════════════════════════════
#  spend_tracker — HF flush outside the lock
# ═════════════════════════════════════════════════════════════════════════════

def test_flush_runs_without_holding_the_lock():
    from core import spend_tracker as st
    tracker = st.spend_tracker

    lock_held_during_flush = []

    def fake_append(record, *args, **kwargs):
        lock_held_during_flush.append(tracker._lock.locked())
        return True

    with patch.object(st, "append_record", fake_append):
        # Force the flush threshold on the next record_cost
        tracker._calls_since_flush = st._FLUSH_INTERVAL_CALLS - 1
        tracker._persistence_ok = True
        tracker.record_cost("deepseek", "deepseek-v4-flash", 100, 100)

    assert lock_held_during_flush == [False]


# ═════════════════════════════════════════════════════════════════════════════
#  legisinfo find_bills — cached fetch + id-less matches kept
# ═════════════════════════════════════════════════════════════════════════════

def test_find_bills_uses_session_cache_and_keeps_idless_match():
    from local_tools import legisinfo_api as li
    rec = {
        "BillNumberFormatted": "C-22",
        "LongTitleEn": "Some act",
        "ParliamentNumber": 44,
        "SessionNumber": 1,
        # no BillId / Id on purpose
    }
    with patch.object(li, "fetch_legisinfo_bills", MagicMock(return_value=[rec])) as mock_fetch, \
         patch.object(li, "_fetch_json", MagicMock()) as mock_raw:
        results = li.find_bills("C-22", year=2022)  # 2022 maps to session 44-1 only

    assert len(results) == 1  # id-less record must not be silently dropped
    assert results[0]["BillNumberFormatted"] == "C-22"
    assert mock_fetch.call_count == 1
    mock_raw.assert_not_called()  # cache path, never the raw downloader


def test_find_bills_skips_non_dict_records():
    from local_tools import legisinfo_api as li
    rec = {"BillNumberFormatted": "C-22", "BillId": 7}
    with patch.object(li, "fetch_legisinfo_bills", MagicMock(return_value=["junk", None, rec])):
        results = li.find_bills("C-22", year=2021)
    assert results == [rec]


# ═════════════════════════════════════════════════════════════════════════════
#  mcgill_engine — typed DOI/ISBN sentinels
# ═════════════════════════════════════════════════════════════════════════════

def test_journal_article_without_doi_raises_typed_sentinel():
    from core.mcgill_engine import format_citation, NotADoiError
    with pytest.raises(NotADoiError):
        format_citation({"raw_text": "definitely not a doi"}, doc_type="journal_article")


def test_book_with_bad_checksum_isbn_raises_typed_sentinel():
    from core.mcgill_engine import format_citation, InvalidIsbnError
    with pytest.raises(InvalidIsbnError):
        format_citation({"raw_text": "9780132350885"}, doc_type="book")


def test_isbn_in_doi_field_autodetect_still_works():
    """A valid ISBN typed into the DOI field must still route to the book
    path (sentinel-based, not message-matching)."""
    with patch("core.mcgill_engine.fetch_openlibrary", MagicMock(return_value=None)):
        resp = client.post("/api/extract/url", json={"doi": "9780132350884"})

    body = resp.json()
    # OpenLibrary unreachable → the ISBN branch produced its degrade response
    # (NOT the DOI error), proving the ISBN auto-detect fired.
    assert body["error"]["reason"].startswith("We couldn't process this ISBN")


def test_plain_valueerror_no_longer_triggers_isbn_autodetect():
    """A generic ValueError (not the sentinel) must NOT trigger the ISBN path."""
    class FakeValueError(ValueError):
        pass
    with patch("api.main.format_citation", MagicMock(side_effect=FakeValueError(
            "This doesn't look like a valid DOI — please check the identifier."))), \
         patch("api.main.extract_isbn", MagicMock(return_value="9780132350884")) as mock_isbn:
        resp = client.post("/api/extract/url", json={"doi": "10.9999/nope"})

    mock_isbn.assert_not_called()
    body = resp.json()
    assert body["status"] == "unsupported"
    assert "DOI" in body["error"]["reason"]
