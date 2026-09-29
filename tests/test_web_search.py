"""The web plugin: Exa search, blocked-searcher honesty, and page reading."""
import json
from unittest.mock import patch

import pytest

from harness.core import Harness
from harness.plugin import discover
from plugins import web


@pytest.fixture
def h(tmp_path):
    return Harness(discover(), model="m", config_path=tmp_path / "h.json")


@pytest.fixture
def session(h):
    session, _ = h.sessions.start()
    h.set_enabled("web", True)
    session.loaded.append("web")
    return session


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


EXA_HIT = {"title": "Suspect, officer wounded outside Belleville synagogue",
           "url": "https://www.cbc.ca/news/canada/belleville-9.7351776",
           "publishedDate": "2026-09-21T14:02:11.000Z", "author": "Amy van den Berg",
           "text": "Exchange of gunfire came as members of the Jewish community gathered."}


def setup_exa(h, key="k-exa-123"):
    h.set_plugin_settings("web", {"provider": "exa", "exa_api_key": key})


def test_exa_search_returns_dated_results(h, session):
    setup_exa(h)
    with patch("local_tools.utils.request_with_retry",
               return_value=FakeResponse(payload={"results": [EXA_HIT]})) as mock:
        result = web.search(h.context(session, "web"),
                            web.SearchParams(query="Belleville synagogue shooting", latest_days=30))
    assert result.content["results"][0]["date"] == "2026-09-21"
    assert result.content["results"][0]["author"] == "Amy van den Berg"
    assert result.content["note"] == ""
    body = mock.call_args.kwargs["json"]
    assert body["numResults"] == 6 and body["startPublishedDate"]  # the freshness filter was sent


def test_exa_without_a_key_asks_the_user_and_touches_no_network(h, session):
    h.set_plugin_settings("web", {"provider": "exa"})
    with patch("local_tools.utils.request_with_retry", side_effect=AssertionError("no network here")):
        with patch.dict("os.environ", {"EXA_API_KEY": ""}, clear=False):
            result = web.search(h.context(session, "web"), web.SearchParams(query="Ottawa shooting"))
    assert result.final is True and "EXA_API_KEY" in result.blocks[0]["text"]


def test_exa_key_falls_back_to_the_env_when_no_setting_is_pasted(h, session):
    """The .env is the primary home for the key: EXA_API_KEY works without
    the settings bar, and the pasted setting value wins when both exist."""
    h.set_plugin_settings("web", {"provider": "exa"})
    with patch.dict("os.environ", {"EXA_API_KEY": "k-from-env"}):
        with patch("local_tools.utils.request_with_retry",
                   return_value=FakeResponse(payload={"results": [EXA_HIT]})) as mock:
            result = web.search(h.context(session, "web"), web.SearchParams(query="Ottawa shooting"))
    assert result.content["results"][0]["date"] == "2026-09-21"
    assert mock.call_args.kwargs["headers"]["x-api-key"] == "k-from-env"
    # A key pasted in the settings bar overrides the env one.
    setup_exa(h, key="k-from-settings")
    with patch.dict("os.environ", {"EXA_API_KEY": "k-from-env"}):
        with patch("local_tools.utils.request_with_retry",
                   return_value=FakeResponse(payload={"results": [EXA_HIT]})) as mock:
            web.search(h.context(session, "web"), web.SearchParams(query="Ottawa shooting"))
    assert mock.call_args.kwargs["headers"]["x-api-key"] == "k-from-settings"


def test_exa_bad_key_says_so_through_the_harness(h, session):
    setup_exa(h, key="bad")
    with patch("local_tools.utils.request_with_retry", return_value=FakeResponse(status_code=401)):
        call = {"id": "c1", "type": "function", "function": {
            "name": "web__search", "arguments": json.dumps({"query": "Ottawa shooting"})}}
        result = h._execute(session, call)[1]
    assert result.content["error"] == "The API key is invalid or unauthorized"


def test_a_blocked_duckduckgo_says_so_instead_of_reporting_zero(h):
    page = ('<link rel="canonical" href="https://duckduckgo.com/">'
            '<html><head><title>DuckDuckGo</title></head><body>anomaly detected</body></html>')
    with patch("local_tools.utils.request_with_retry",
               return_value=FakeResponse(status_code=202, text=page)):
        with pytest.raises(ValueError) as exc:
            web.duckduckgo_lite("Ottawa shooting", 6)
    assert "bot-check" in str(exc.value)


def test_two_empty_searches_warn_the_model_to_stop_retrying(h, session):
    h.set_plugin_settings("web", {"provider": "duckduckgo_lite"})
    empty_page = "<html><body><table><tr><td class='result-snippet'></td></tr></table></body></html>"
    ctx = h.context(session, "web")
    with patch("local_tools.utils.request_with_retry",
               return_value=FakeResponse(text=empty_page)):
        first = web.search(ctx, web.SearchParams(query="one"))
        second = web.search(ctx, web.SearchParams(query="two"))
    assert first.content["note"] == ""
    assert "2 searches in a row" in second.content["note"]
    assert "fetch" in second.content["note"]


def test_a_search_attempt_sets_the_flag_that_opens_record_compose(h, session):
    """Even a blocked search discharges the model's duty to try the web:
    without this, a broken key would lock the model out of record__compose
    forever, and the user could not hand over values either."""
    from harness.core import BUILTIN_TOOLS

    h.set_plugin_settings("web", {"provider": "duckduckgo_lite"})
    with patch("local_tools.utils.request_with_retry",
               return_value=FakeResponse(status_code=202, text="<html>challenge</html>")):
        with pytest.raises(ValueError):
            web.search(h.context(session, "web"), web.SearchParams(query="Ottawa shooting"))
    assert session.web_search_used is True
    compose = BUILTIN_TOOLS[0]
    result = h._run_builtin(session, compose, {"record_type": "jurisprudence",
                                               "fields": {"style_of_cause": {"value": "R v X"}}},
                            "record__compose")[1]
    assert result.content["ref"] == "rec_1"                   # the gate opened


def test_fetch_keeps_the_page_date_and_strips_the_site_suffix(h, session):
    page = {"page_title": "Suspect, officer wounded outside Belleville synagogue | CBC News",
            "site_name": "CBC News", "date": "2026-09-21", "author": "Amy van den Berg",
            "raw_text": "Exchange of gunfire came as members gathered for Yom Kippur."}
    with patch("local_tools.web_extract.extract_from_url", return_value=page):
        result = web.fetch(h.context(session, "web"),
                           web.FetchParams(url="https://www.cbc.ca/news/canada/belleville-9.7351776"))
    assert result.content["title"] == "Suspect, officer wounded outside Belleville synagogue"
    assert result.content["date"] == "2026-09-21"
    record = session.records.get(result.content["record"]["ref"])
    assert record.fields["date"].value == "2026-09-21" and record.fields["date"].origin == "extracted"
    assert record.fields["author"].origin == "extracted"
    # And the stored record cites with its date, honestly unverified.
    from plugins import mcgill
    citation = mcgill.cite(h.context(session, "mcgill"),
                           mcgill.CiteParams(ref=result.content["record"]["ref"]))
    assert "(21 September 2026)" in citation.content["citation"]
    assert citation.content["verified"] is False


def test_fetch_strips_the_site_suffix_even_when_site_name_is_shorter(h, session):
    """Regression, seen live: og:site_name gives the brand alone ("CBC")
    while the <title> tag's own chrome is longer ("CBC News") -- an exact
    match missed this and left "| CBC News" baked into the stored title and
    every citation rendered from it."""
    page = {"page_title": "Man who murdered 7-week-old baby can't apply for parole for 15 years | CBC News",
            "site_name": "CBC", "date": "2026-07-02", "author": "Kristy Nease",
            "raw_text": "For murder in the second degree of a baby who was seven weeks old..."}
    with patch("local_tools.web_extract.extract_from_url", return_value=page):
        result = web.fetch(h.context(session, "web"),
                           web.FetchParams(url="https://www.cbc.ca/news/canada/ottawa/x-9.7254177"))
    assert result.content["title"] == "Man who murdered 7-week-old baby can't apply for parole for 15 years"


def test_fetch_still_works_when_the_page_has_no_metadata(h, session):
    page = {"page_title": "A bare page", "raw_text": "Some text only."}
    with patch("local_tools.web_extract.extract_from_url", return_value=page):
        result = web.fetch(h.context(session, "web"), web.FetchParams(url="https://example.com/page"))
    assert result.content["title"] == "A bare page"
    record = session.records.get(result.content["record"]["ref"])
    assert set(record.fields) == {"title", "url", "text"}


def test_a_bot_check_page_is_refused_not_stored(h, session):
    """bailii's Anubis page extracts as readable text -- it must never land in
    the session as a record that pretends to be evidence."""
    page = {"page_title": "Making sure you're not a bot!",
            "raw_text": "Making sure you're not a bot! Loading... This server is protected."}
    with patch("local_tools.web_extract.extract_from_url", return_value=page):
        with pytest.raises(ValueError) as exc:
            web.fetch(h.context(session, "web"), web.FetchParams(url="http://www.bailii.org/uk/cases/UKHL/1932/100.html"))
    assert "bot-check" in str(exc.value)
    assert session.records.all() == []


@pytest.mark.parametrize("settings", [{"provider": ""}, {"provider": "exa", "exa_api_key": ""}])
def test_a_search_that_cannot_run_still_opens_record_compose(h, session, settings, monkeypatch):
    """No service chosen, or Exa with no key: the search cannot run, and
    record__compose must not wait forever for one that will never happen."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    h.set_plugin_settings("web", settings)
    result = web.search(h.context(session, "web"), web.SearchParams(query="Ottawa shooting"))
    assert "error" in result.content
    assert session.web_search_used is True
