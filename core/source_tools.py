"""Contract adapters for the existing legal source connectors.

These functions deliberately contain no registration or network implementation.
They turn connector responses into ``Record`` objects and fail closed when a
source cannot provide a real record identifier.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from core.tool_contracts import Field, Record
from local_tools.a2aj_api import _map_fields, fetch_by_citation, search_cases_multi, search_laws_by_name
from local_tools.crossref_api import extract_doi, fetch_crossref
from local_tools.legisinfo_api import find_bills
from local_tools.openlibrary_api import extract_isbn, fetch_openlibrary, validate_isbn


class SourceContractError(ValueError):
    """A connector result cannot be represented as a traceable database record."""


def _source_id(provider: str, record_id: Any, kind: str) -> str:
    if record_id is None or not str(record_id).strip():
        raise SourceContractError(f"{kind} result has no real record identifier")
    return f"{provider}:{str(record_id).strip()}"


def _provider_and_id(item: Mapping[str, Any], kind: str) -> tuple[str, str, str]:
    """Use a connector ID or its original URL; never invent an identifier."""
    url = item.get("url") or item.get("url_en") or item.get("source_url_en") or item.get("longUrl")
    if url:
        text_url = str(url).strip()
        provider = "a2aj"  # The lookup provider; URL is the underlying source document.
        return provider, text_url, _source_id(provider, text_url, kind)
    provider = "a2aj"
    record_id = item.get("id") or item.get("case_id") or item.get("statute_id")
    return provider, str(record_id).strip() if record_id is not None else "", _source_id(provider, record_id, kind)


def _database_results(results: Any) -> list[Mapping[str, Any]]:
    """Drop explicit unverified/error candidates from legacy connector output."""
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, Mapping)
            and ("verified" not in item or item.get("verified") is True)
            and "warning" not in item and "error" not in item]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _fields(values: Mapping[str, Any], source_id: str) -> dict[str, Field]:
    return {
        name: Field(_text(value), "database", source_id=source_id)
        for name, value in values.items()
        if value is not None and _text(value) != ""
    }


def _case_record(item: Mapping[str, Any]) -> Record:
    provider, record_id, source_id = _provider_and_id(item, "case")
    mapped = item if "style_of_cause" in item else _map_fields(dict(item))
    values = {name: mapped.get(name) for name in
              ("style_of_cause", "neutral_citation", "reporter", "court", "year", "date", "url")}
    fields = _fields(values, source_id)
    if not fields:
        raise SourceContractError("case result has no database fields")
    return Record("jurisprudence", fields, provider, record_id)


def _legislation_record(item: Mapping[str, Any]) -> Record:
    provider, record_id, source_id = _provider_and_id(item, "legislation")
    mapped = item if "title" in item or "statute_title" in item else _map_fields(dict(item))
    values = {
        "title": mapped.get("title") or mapped.get("statute_title"),
        "citation": mapped.get("citation"),
        "jurisdiction": mapped.get("jurisdiction"),
        "chapter": mapped.get("chapter"),
        "year": mapped.get("year"),
        "url": mapped.get("url"),
    }
    fields = _fields(values, source_id)
    if not fields:
        raise SourceContractError("legislation result has no database fields")
    return Record("legislation", fields, provider, record_id)


def source_cases(query: str) -> list[Record]:
    """Search cases directly and return traceable database records."""
    from local_tools.a2aj_api import _CASE_CITATION_RE
    if _CASE_CITATION_RE.fullmatch(query.strip()):
        found = fetch_by_citation(query)
        results = _database_results([found]) if found.get("style_of_cause") else []
    else:
        results = _database_results(search_cases_multi(query, size=5))
    return [_case_record(item) for item in results]


def source_legislation(query: str) -> list[Record]:
    """Search legislation directly and return traceable database records."""
    from local_tools.utils import _CITATION_REGEX
    match = _CITATION_REGEX.search(query)
    if match:
        found = fetch_by_citation(match.group(0), doc_type="laws")
        results = _database_results([found]) if found.get("statute_title") else []
    else:
        results = _database_results(search_laws_by_name(query))
    return [_legislation_record(item) for item in results]


_BILL_NUMBER_RE = re.compile(r"\s*(?:bill\s+)?([CS])\s*-?\s*(\d+)(?:\s+(\d{4}))?\s*", re.I)


def source_bills(query: str) -> list[Record]:
    """Find federal bills in LEGISinfo by bill number (and optional year text)."""
    match = _BILL_NUMBER_RE.fullmatch(query)
    if not match:
        raise SourceContractError("Supply a bill number such as C-22, optionally followed by its year")
    number = f"{match[1].upper()}-{match[2]}"
    results = find_bills(number, year=int(match[3])) if match[3] else find_bills(number)
    return _bill_records(results)


def source_bill_keyword(keywords: str, limit: int = 5) -> list[Record]:
    """Federal bills whose title contains the keywords, newest first.

    The full session list comes from LEGISinfo either way; the filter runs
    on it locally. A bill is kept only when every content word of the
    query appears in its title, so the user still chooses among matches.
    """
    from local_tools.legisinfo_api import fetch_legisinfo_bills

    def tokens(text: str) -> set[str]:
        return {word.casefold() for word in re.findall(r"[^\W\d_]+", text) if len(word) > 2}

    wanted = tokens(keywords)
    if not wanted:
        raise SourceContractError("Supply keywords from the bill's title")
    try:
        bills = fetch_legisinfo_bills()
    except Exception:
        return []
    scored = []
    for item in bills:
        if not isinstance(item, Mapping):
            continue
        title = item.get("LongTitleEn") or item.get("ShortTitleEn") or ""
        if wanted and wanted <= tokens(title):
            date = item.get("LatestCompletedMajorActivityDateTime") or item.get("IntroducedDateTime") or ""
            scored.append((str(date), item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return _bill_records([item for _, item in scored[:limit]])


def _bill_records(items: list) -> list[Record]:
    records = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        record_id = item.get("BillId") or item.get("Id") or item.get("BillID")
        if not record_id and item.get("ParliamentNumber") and item.get("SessionNumber") and item.get("BillNumberFormatted"):
            record_id = f"{item['ParliamentNumber']}-{item['SessionNumber']}/{item['BillNumberFormatted']}"
        source_id = _source_id("legisinfo", record_id, "bill")
        values = {
            "number": item.get("BillNumberFormatted") or item.get("NumberCode"),
            "title": item.get("LongTitleEn") or item.get("ShortTitleEn"),
            "parliament": item.get("ParliamentNumber"),
            "session": item.get("SessionNumber"),
            "session_code": item.get("ParlSessionCode"),
            "introduced": item.get("IntroducedDateTime"),
            "year": next((_year(item.get(key)) for key in ("PassedHouseFirstReadingDateTime", "PassedSenateFirstReadingDateTime", "IntroducedDateTime") if _year(item.get(key))), ""),
        }
        fields = _fields(values, source_id)
        if not fields:
            raise SourceContractError("bill result has no database fields")
        records.append(Record("bill", fields, "legisinfo", str(record_id).strip()))
    return records


def source_doi(query: str) -> list[Record]:
    """Fetch Crossref metadata for a DOI and return one traceable record."""
    doi = extract_doi(query)
    if not doi:
        raise SourceContractError("query does not contain a DOI")
    item = fetch_crossref(doi)
    if not isinstance(item, Mapping):
        return []
    source_id = _source_id("crossref", doi, "DOI")
    values = {
        "doi": doi,
        "title": (item.get("title") or [None])[0] if isinstance(item.get("title"), list) else item.get("title"),
        "author": _format_authors(item.get("author")),
        "journal": (item.get("container-title") or [None])[0]
        if isinstance(item.get("container-title"), list) else item.get("container-title"),
        "year": _published_year(item.get("published")),
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "first_page": _first_page(item.get("page")),
    }
    fields = _fields(values, source_id)
    return [Record("journal_article", fields, "crossref", doi)]


def source_isbn(query: str) -> list[Record]:
    """Fetch Open Library metadata for an ISBN and return one traceable record."""
    isbn = extract_isbn(query)
    if not isbn or not validate_isbn(isbn):
        raise SourceContractError("query does not contain a valid ISBN")
    item = fetch_openlibrary(isbn)
    if not isinstance(item, Mapping):
        return []
    source_id = _source_id("openlibrary", isbn, "ISBN")
    values = {
        "isbn": isbn,
        "title": item.get("title"),
        "author": _format_openlibrary_authors(item.get("authors")),
        "publisher": _first_name(item.get("publishers")),
        "place": _first_name(item.get("publish_places")),
        "year": _year(item.get("publish_date")),
        "edition": item.get("edition_name"),
    }
    fields = _fields(values, source_id)
    return [Record("book", fields, "openlibrary", isbn)]


def source_book_title(query: str, limit: int = 3) -> list[Record]:
    """Book candidates for a free-text title, one full record per candidate."""
    from core.bibliographic import book_values, fetch_edition, search_books
    records = []
    for candidate in search_books(query, limit):
        edition = fetch_edition(candidate["olid"])
        if not isinstance(edition, Mapping):
            continue
        values = book_values(edition)
        if not values.get("title"):
            continue
        source_id = _source_id("openlibrary", candidate["olid"], "OLID")
        records.append(Record("book", _fields(values, source_id), "openlibrary", candidate["olid"]))
    return records


def source_article_title(query: str, limit: int = 3) -> list[Record]:
    """Journal-article candidates for a free-text title, one record per DOI."""
    from core.bibliographic import article_values, search_articles
    records = []
    for candidate in search_articles(query, limit):
        item = fetch_crossref(candidate["doi"])
        if not isinstance(item, Mapping):
            continue
        source_id = _source_id("crossref", candidate["doi"], "DOI")
        values = {**article_values(item), "doi": candidate["doi"]}
        records.append(Record("journal_article", _fields(values, source_id), "crossref",
                              candidate["doi"]))
    return records


def _published_year(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            return _text(parts[0][0])
    return ""


def _first_page(value: Any) -> str:
    return _text(value).split("-")[0].strip() if value else ""


def _format_authors(authors: Any) -> str:
    if not isinstance(authors, list):
        return ""
    names = [" ".join(str(part).strip() for part in (a.get("given"), a.get("family")) if part and str(part).strip())
             for a in authors if isinstance(a, Mapping)]
    names = [name for name in names if name]
    if len(names) > 3:
        return names[0] + " et al"
    if len(names) == 2:
        return " & ".join(names)
    if len(names) == 3:
        return f"{names[0]}, {names[1]} & {names[2]}"
    return names[0] if names else ""


def _first_name(values: Any) -> str:
    if isinstance(values, list) and values and isinstance(values[0], Mapping):
        return _text(values[0].get("name"))
    return ""


def _format_openlibrary_authors(authors: Any) -> str:
    if not isinstance(authors, list):
        return ""
    names = [_text(a.get("name")) for a in authors if isinstance(a, Mapping) and a.get("name")]
    return " & ".join(names[:2]) if len(names) <= 2 else names[0] + " et al"


def _year(value: Any) -> str:
    import re
    match = re.search(r"\b[12]\d{3}\b", _text(value))
    return match.group(0) if match else ""
