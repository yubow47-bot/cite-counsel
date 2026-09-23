"""The data-source plugins: connectors mocked, contract output real."""
from unittest.mock import patch

from harness.core import Harness
from harness.plugin import discover
from harness.session import Context


def make():
    h = Harness(discover(), model="m")
    session, _ = h.sessions.start()
    return h, session


def ctx_for(h, session, name):
    return Context(session, name, h)


def test_a2aj_find_case_stores_a_database_record():
    h, session = make()
    from plugins import a2aj
    with patch("core.source_tools.search_cases_multi", return_value=[{
        "id": "case-17", "name_en": "Example v Example", "citation_en": "2024 SCC 1",
        "verified": True,
    }]):
        result = a2aj.find_case(ctx_for(h, session, "a2aj"), a2aj.CaseParams(query="Example v Example"))
    assert result.final is True
    ref = result.content["records"][0]["ref"]
    record = session.records.get(ref)
    assert record.source_type == "jurisprudence"
    assert record.fields["style_of_cause"].origin == "database"
    assert record.fields["style_of_cause"].source_id.startswith("a2aj:")


def test_a2aj_no_match_says_what_was_searched():
    h, session = make()
    from plugins import a2aj
    with patch("core.source_tools.search_cases_multi", return_value=[]):
        result = a2aj.find_case(ctx_for(h, session, "a2aj"), a2aj.CaseParams(query="Miranda"))
    assert result.content["found"] == 0
    assert result.final is False
    assert any(b["type"] == "notice" for b in result.blocks)


def test_legisinfo_keyword_search_filters_by_title():
    h, session = make()
    from plugins import legisinfo
    bills = [
        {"BillId": 1, "BillNumberFormatted": "C-22", "LongTitleEn": "An Act respecting artificial intelligence",
         "ParliamentNumber": 44, "SessionNumber": 1, "IntroducedDateTime": "2022-06-16"},
        {"BillId": 2, "BillNumberFormatted": "S-3", "LongTitleEn": "An Act to amend the Customs Act",
         "ParliamentNumber": 44, "SessionNumber": 1, "IntroducedDateTime": "2021-12-09"},
    ]
    with patch("local_tools.legisinfo_api.fetch_legisinfo_bills", return_value=bills):
        result = legisinfo.search_bills(ctx_for(h, session, "legisinfo"),
                                        legisinfo.BillsParams(keywords="artificial intelligence"))
    assert result.content["found"] == 1
    record = session.records.get(result.content["records"][0]["ref"])
    assert record.fields["number"].value == "C-22"
    assert record.fields["number"].source_id == "legisinfo:1"


def test_legisinfo_bill_number_keeps_its_record_id():
    h, session = make()
    from plugins import legisinfo
    with patch("core.source_tools.find_bills", return_value=[{
        "BillId": 42, "BillNumberFormatted": "C-22", "LongTitleEn": "An Act",
        "ParliamentNumber": 44, "SessionNumber": 1,
    }]):
        result = legisinfo.find_bill(ctx_for(h, session, "legisinfo"), legisinfo.BillParams(number="C-22"))
    record = session.records.get(result.content["records"][0]["ref"])
    assert record.record_id == "42"


def test_crossref_doi_record():
    h, session = make()
    from plugins import crossref
    with patch("core.source_tools.fetch_crossref", return_value={
        "title": ["A paper"], "author": [{"family": "Doe", "given": "Jane"}],
        "container-title": ["Journal"], "published": {"date-parts": [[2024]]},
    }):
        result = crossref.find_doi(ctx_for(h, session, "crossref"),
                                   crossref.DoiParams(doi="10.1234/abcd"))
    record = session.records.get(result.content["records"][0]["ref"])
    assert record.provider == "crossref"
    assert record.fields["doi"].source_id == "crossref:10.1234/abcd"


def test_openlibrary_title_search_uses_olid_ids():
    h, session = make()
    from plugins import openlibrary
    with patch("core.bibliographic.search_books", return_value=[{
        "display": "书 · The Concept of Law", "name": "The Concept of Law",
        "bib_provider": "openlibrary", "olid": "OL123M",
    }]), patch("core.bibliographic.fetch_edition", return_value={
        "title": "The Concept of Law", "authors": [{"name": "H. L. A. Hart"}],
        "publishers": [{"name": "Oxford University Press"}],
        "publish_places": [{"name": "Oxford"}], "publish_date": "2012", "languages": ["eng"],
    }):
        result = openlibrary.find_book(ctx_for(h, session, "openlibrary"),
                                       openlibrary.BookParams(title="Hart The Concept of Law"))
    record = session.records.get(result.content["records"][0]["ref"])
    assert record.source_type == "book"
    assert record.record_id == "OL123M"
    assert record.fields["publisher"].value == "Oxford University Press"
    assert all(f.origin == "database" for f in record.fields.values())
