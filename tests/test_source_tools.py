from unittest.mock import patch

import pytest

from core.source_tools import (
    SourceContractError,
    source_bills,
    source_cases,
    source_doi,
    source_isbn,
    source_legislation,
)


def test_source_cases_maps_database_fields_and_real_id():
    with patch("core.source_tools.search_cases_multi", return_value=[{
        "id": "case-17", "name_en": "Example v Example",
        "citation_en": "2024 SCC 1", "verified": True,
    }]):
        records = source_cases("Example")
    assert records[0].provider == "a2aj"
    assert records[0].record_id == "case-17"
    assert records[0].fields["style_of_cause"].origin == "database"
    assert records[0].fields["style_of_cause"].source_id == "a2aj:case-17"


def test_source_cases_rejects_missing_id():
    with patch("core.source_tools.search_cases_multi", return_value=[{"name_en": "Unidentified"}]):
        with pytest.raises(SourceContractError):
            source_cases("Example")


def test_source_legislation_maps_record():
    with patch("core.source_tools.search_laws_by_name", return_value=[{
        "id": "statute-3", "name_en": "Example Act",
        "citation_en": "S.C. 2024, c 3", "dataset": "LEGISLATION-CA",
    }]):
        records = source_legislation("Example Act")
    assert records[0].source_type == "legislation"
    assert records[0].fields["title"].source_id == "a2aj:statute-3"


def test_source_bills_uses_legisinfo_id():
    with patch("core.source_tools.find_bills", return_value=[{
        "BillId": 42, "BillNumberFormatted": "C-22", "LongTitleEn": "An Act",
        "ParliamentNumber": 44, "SessionNumber": 1,
    }]):
        records = source_bills("C-22")
    assert records[0].provider == "legisinfo"
    assert records[0].record_id == "42"
    assert records[0].fields["number"].value == "C-22"


def test_source_doi_uses_doi_as_real_source_id():
    with patch("core.source_tools.fetch_crossref", return_value={
        "title": ["A paper"], "author": [{"family": "Doe"}],
        "container-title": ["Journal"], "URL": "https://doi.org/10.1234/abcd",
    }):
        records = source_doi("doi:10.1234/abcd")
    assert records[0].fields["doi"].source_id == "crossref:10.1234/abcd"


def test_source_isbn_uses_isbn_as_real_source_id():
    isbn = "9780306406157"
    with patch("core.source_tools.fetch_openlibrary", return_value={"title": "Book"}):
        records = source_isbn(isbn)
    assert records[0].record_id == isbn
    assert records[0].fields["isbn"].source_id == f"openlibrary:{isbn}"


def test_identifier_inputs_are_required():
    with pytest.raises(SourceContractError):
        source_doi("not a DOI")
    with pytest.raises(SourceContractError):
        source_isbn("not an ISBN")
