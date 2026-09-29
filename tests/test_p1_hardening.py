"""Hardening tests: the rate limiter (harness-owned) and legisinfo find_bills.

Run: pytest tests/test_p1_hardening.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from harness.rate_limiter import RateLimiter, _int_env

# ═════════════════════════════════════════════════════════════════════════════
#  Rate limiter — bounded table + env robustness
# ═════════════════════════════════════════════════════════════════════════════

def test_rate_limiter_sweeps_stale_buckets_when_capped():
    limiter = RateLimiter()
    stale = 1.0  # ancient timestamp (well outside both windows)
    for i in range(8):
        limiter._buckets[f"10.0.0.{i}"] = [stale]
    with patch("harness.rate_limiter._MAX_TRACKED_IPS", 5):
        assert limiter.check("fresh-ip") is True
    assert len(limiter._buckets) <= 6  # stale entries evicted, fresh kept
    assert "10.0.0.0" not in limiter._buckets


def test_rate_limiter_env_garbage_falls_back_to_default():
    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "not-a-number"}):
        assert _int_env("RATE_LIMIT_PER_MIN", 30) == 30
    with patch.dict(os.environ, {"RATE_LIMIT_PER_HOUR": "0"}):
        assert _int_env("RATE_LIMIT_PER_HOUR", 200) == 200


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
