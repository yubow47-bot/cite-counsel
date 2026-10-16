"""Source failures, partial searches and stale cache must remain observable."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from local_tools import a2aj_api, legisinfo_api
from local_tools.source_status import SourceRequestError, SourceResults


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


@pytest.mark.parametrize("search", [
    lambda: a2aj_api.search_cases_multi("Example"),
    lambda: a2aj_api.search_laws_by_name("Example Act"),
])
def test_a2aj_timeout_is_not_an_empty_success(search):
    with patch.object(a2aj_api, "request_with_retry", side_effect=requests.exceptions.Timeout()):
        with pytest.raises(SourceRequestError) as error:
            search()
    assert error.value.reason == "timeout"
    assert error.value.retryable is True
    assert error.value.coverage["status"] == "failed"


def test_direct_citation_timeout_reaches_plugin_as_retryable_error():
    from harness.core import Harness
    from harness.plugin import discover
    from harness.session import Context
    from plugins import a2aj
    harness = Harness(discover(), model="m")
    session, _ = harness.sessions.start()
    with patch.object(a2aj_api, "request_with_retry", side_effect=requests.exceptions.Timeout()):
        result = a2aj.find_case(Context(session, "a2aj", harness), a2aj.CaseParams(query="2024 SCC 1"))
    assert result.content["kind"] == "source_unavailable"
    assert result.content["retryable"] is True
    assert "found" not in result.content
    assert session.records.all() == []


def test_a2aj_successful_empty_search_remains_distinct():
    with patch.object(a2aj_api, "request_with_retry", return_value=_response({"results": []})):
        results = a2aj_api.search_cases_multi("No fixture match")
    assert results == []
    assert results.coverage["status"] == "complete"


def test_legisinfo_unavailable_without_cache_raises():
    with patch.object(legisinfo_api, "_CACHE", {}), patch.object(legisinfo_api, "_CACHE_TIME", {}), \
         patch.object(legisinfo_api, "request_with_retry", side_effect=requests.exceptions.Timeout()):
        with pytest.raises(SourceRequestError, match="timeout"):
            legisinfo_api.fetch_legisinfo_bills(session="44-1")


def test_legisinfo_refresh_failure_retains_cache_age_and_stale_flag():
    bill = {"BillId": 1, "BillNumberFormatted": "C-22"}
    with patch.object(legisinfo_api, "_CACHE", {"44-1": [bill]}), \
         patch.object(legisinfo_api, "_CACHE_TIME", {"44-1": 100.0}), \
         patch.object(legisinfo_api.time, "time", return_value=5000.0), \
         patch.object(legisinfo_api, "request_with_retry", side_effect=requests.exceptions.Timeout()):
        results = legisinfo_api.fetch_legisinfo_bills(session="44-1")
    assert results == [bill]
    assert results.coverage["status"] == "partial"
    assert results.coverage["stale"] is True
    assert results.coverage["cache_age_seconds"] == 4900.0
    assert results.coverage["refresh_error"] == "timeout"


def test_empty_successful_session_does_not_count_as_failed():
    with patch.object(legisinfo_api, "fetch_legisinfo_bills", return_value=[]):
        results = legisinfo_api.find_bills("C-22", year=1997)
    assert results == []
    assert set(results.coverage["completed_sessions"]) == {"35-2", "36-1"}
    assert results.coverage["status"] == "complete"
    assert results.coverage["failed_sessions"] == []


def test_partial_session_failure_preserves_coverage_even_without_matches():
    def fetch(session):
        if session == "35-2":
            raise SourceRequestError("legisinfo", "bill list", reason="timeout")
        return []
    with patch.object(legisinfo_api, "fetch_legisinfo_bills", side_effect=fetch):
        results = legisinfo_api.find_bills("C-22", year=1997)
    assert results == []
    assert results.coverage["status"] == "partial"
    assert results.coverage["failed_sessions"] == ["35-2"]


def test_french_title_search_returns_traceable_french_title_and_status():
    from core.source_tools import source_bill_keyword
    bills = SourceResults([{
        "BillId": 7, "BillNumberFormatted": "C-7", "LongTitleEn": "An Act respecting data",
        "LongTitleFr": "Loi sur la protection des données",
        "CurrentStatusEn": "At third reading",
        "CurrentStatusFr": "À la troisième lecture",
        "LatestCompletedMajorActivityEn": "Second reading",
        "LatestCompletedMajorActivityFr": "Deuxième lecture",
        "LatestCompletedMajorActivityDateTime": "2024-02-03",
    }], coverage={"status": "partial", "stale": True, "cache_age_seconds": 4900})
    with patch.object(legisinfo_api, "fetch_legisinfo_bills", return_value=bills):
        results = source_bill_keyword("protection donnees")
    assert len(results) == 1
    assert results[0].fields["title_fr"].value == "Loi sur la protection des données"
    assert results[0].fields["status"].value == "At third reading"
    assert results[0].fields["latest_activity"].value == "Second reading"
    assert results[0].fields["latest_activity_date"].value == "2024-02-03"
    assert results[0].fields["status"].source_id == "legisinfo:7"
    assert results.coverage["stale"] is True


def test_partial_coverage_is_saved_and_visible_in_plugin_result():
    from harness.core import Harness
    from harness.plugin import discover
    from harness.session import Context
    from plugins import legisinfo

    harness = Harness(discover(), model="m")
    session, _ = harness.sessions.start()
    bills = SourceResults([{"BillId": 9, "BillNumberFormatted": "C-22", "LongTitleEn": "An Act"}],
                          coverage={"status": "partial", "failed_sessions": ["35-2"]})
    with patch("core.source_tools.find_bills", return_value=bills):
        result = legisinfo.find_bill(Context(session, "legisinfo", harness), legisinfo.BillParams(number="C-22"))
    assert result.content["coverage"]["status"] == "partial"
    assert any(block["type"] == "notice" for block in result.blocks)
    reference = result.content["records"][0]["ref"]
    assert session.records.meta(reference)["source_coverage"]["failed_sessions"] == ["35-2"]

