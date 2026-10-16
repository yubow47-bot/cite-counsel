"""Bibliographic sources distinguish a lookup miss from an unavailable service."""

from unittest.mock import patch

import pytest
import requests

from core import bibliographic
from harness.core import Harness
from harness.session import Context
from local_tools import crossref_api, openlibrary_api
from local_tools.source_status import SourceRequestError
from plugins import crossref, openlibrary


def response(payload=None, status=200):
    class Response:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return payload

    return Response()


@pytest.mark.parametrize("plugin,params,connector", [
    (crossref.find_doi, crossref.DoiParams(doi="10.1234/abcd"), "local_tools.crossref_api.request_with_retry"),
    (openlibrary.find_isbn, openlibrary.IsbnParams(isbn="9780804429573"), "local_tools.openlibrary_api.request_with_retry"),
    (crossref.find_article, crossref.ArticleParams(title="A legal article"), "core.bibliographic.request_with_retry"),
    (openlibrary.find_book, openlibrary.BookParams(title="A legal book"), "core.bibliographic.request_with_retry"),
])
def test_timeout_reaches_plugin_as_retryable_source_error(tmp_path, plugin, params, connector):
    h = Harness({p.name: p for p in (crossref.PLUGIN, openlibrary.PLUGIN)},
                model="offline", config_path=tmp_path / "harness.json")
    session, _ = h.sessions.start()
    with patch(connector, side_effect=requests.Timeout("private upstream detail")):
        result = plugin(Context(session, "crossref" if plugin.__module__.endswith("crossref") else "openlibrary", h), params)
    assert result.content["kind"] == "source_unavailable"
    assert result.content["reason"] == "timeout"
    assert result.content["retryable"] is True
    assert "found" not in result.content
    assert "private upstream detail" not in str(result.content)
    assert result.blocks[0]["level"] == "warning"
    assert session.records.all() == []


@pytest.mark.parametrize("lookup,connector", [
    (lambda: crossref_api.fetch_crossref("10.1234/absent"), crossref_api),
    (lambda: openlibrary_api.fetch_openlibrary("9780804429573"), openlibrary_api),
])
def test_real_http_not_found_is_a_lookup_miss(lookup, connector):
    with patch.object(connector, "request_with_retry", return_value=response(status=404)):
        assert lookup() is None


def test_openlibrary_can_recover_from_books_api_failure_using_edition():
    with patch.object(openlibrary_api, "request_with_retry", side_effect=[
        requests.Timeout(), response({"title": "Recovered edition"})
    ]):
        assert openlibrary_api.fetch_openlibrary("9780804429573")["title"] == "Recovered edition"


def test_openlibrary_fallback_miss_does_not_erase_primary_failure():
    with patch.object(openlibrary_api, "request_with_retry", side_effect=[
        requests.Timeout(), response(status=404)
    ]):
        with pytest.raises(SourceRequestError) as error:
            openlibrary_api.fetch_openlibrary("9780804429573")
    assert error.value.reason == "timeout"


def test_openlibrary_author_timeout_does_not_silently_drop_the_author():
    with patch.object(openlibrary_api, "request_with_retry", side_effect=[
        response({"title": "Edition", "authors": [{"key": "/authors/OL1A"}]}), requests.Timeout()
    ]):
        with pytest.raises(SourceRequestError) as error:
            openlibrary_api.fetch_edition_data("books/OL1M")
    assert error.value.operation == "author metadata"


@pytest.mark.parametrize("search,connector,payload", [
    (lambda: crossref_api.fetch_crossref("10.1234/abcd"), crossref_api, {"message": "invalid"}),
    (lambda: openlibrary_api.fetch_edition_data("books/OL1M"), openlibrary_api, []),
    (lambda: bibliographic.search_books("A legal book"), bibliographic, {"docs": "invalid"}),
    (lambda: bibliographic.search_articles("A legal article"), bibliographic, {"message": {"items": None}}),
])
def test_invalid_metadata_and_search_envelopes_are_source_errors(search, connector, payload):
    with patch.object(connector, "request_with_retry", return_value=response(payload)):
        with pytest.raises(SourceRequestError) as error:
            search()
    assert error.value.reason == "invalid_response"


@pytest.mark.parametrize("search,payload", [
    (bibliographic.search_books, {"docs": []}),
    (bibliographic.search_articles, {"message": {"items": []}}),
])
def test_successful_empty_title_search_remains_a_miss(search, payload):
    with patch.object(bibliographic, "request_with_retry", return_value=response(payload)):
        assert search("No matching title") == []
