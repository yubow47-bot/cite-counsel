"""Books and journal articles found by title, not only by ISBN or DOI.

Law students cite secondary sources as often as cases, and usually by title
("Hart The Concept of Law"). The case-law and legislation databases were never
going to hold these, so the Chatbox used to stop and ask which kind of source
it was. Open Library and Crossref are searchable by title: a match there is a
real catalogue record with a stable identifier, which makes it a database
source like any other.

Everything here copies values from those records. A catalogue search is fuzzy
and always returns *something*, so results are kept only when the record's own
title is essentially contained in what the user typed; the user still chooses
between the survivors. Nothing is ever guessed to fill a gap: a record without
a place of publication leaves the place for the user to supply, which makes
the citation unverified.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from local_tools.utils import crossref_session, openlibrary_session, request_with_retry

_STOPWORDS = {"the", "and", "for", "with", "from", "into", "des", "les", "une", "und", "der", "die"}
_OL_FIELDS = ("key,title,subtitle,author_name,editions,editions.key,editions.title,editions.subtitle,"
              "editions.publisher,editions.publish_date")
_CROSSREF_HEADERS = {"User-Agent": "CiteCounsel/0.1 (local research tool)"}
MIN_TITLE_OVERLAP = 0.75
MIN_QUERY_COVERAGE = 0.6
MAX_EXTRA_QUERY_WORDS = 2      # an author's name, a year


def _tokens(text: str) -> set[str]:
    """Casefolded content words, with diacritics folded (Upaniṣads == Upanisads)."""
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    return {word for word in re.findall(r"[a-z0-9]+", folded) if len(word) > 2 and word not in _STOPWORDS}


def title_matches(title: str, query: str) -> bool:
    """The record and the query must name the same work -- checked both ways.

    The record's own title must be (almost) all present in the user's words: a
    record whose title the user did not type is a different work. And the
    query must be (mostly) covered by the record: a long, specific title is
    not matched by a short generic one that merely shares its topic words
    ("Indigenous Peoples, Self-determination and International Law" is 5/6
    inside "Columbus's Legacy: Law as an Instrument of Racial Discrimination
    against Indigenous Peoples' Rights of Self-Determination", but covers only
    5 of its 12 words). The query may still add an author or a year: up to
    MAX_EXTRA_QUERY_WORDS uncovered words are allowed whatever the ratio.

    A pasted full citation ("H.L.A. Hart, The Concept of Law, 3rd ed (Oxford:
    Oxford University Press, 2012)") carries far more than two extra words.
    When the query has a year in it, one of its parts -- split at commas,
    brackets and quotation marks -- that is essentially the record's title
    is a match too. A bare title with no year never takes that path, so a
    title with a comma in it is not split into short generic ones.
    """
    wanted, typed = _tokens(title), _tokens(query)
    if not wanted or not typed:
        return False
    shared = len(wanted & typed)
    if shared / len(wanted) < MIN_TITLE_OVERLAP:
        return False
    if len(typed) - shared <= MAX_EXTRA_QUERY_WORDS or shared / len(typed) >= MIN_QUERY_COVERAGE:
        return True
    if not _YEAR_IN_CITATION.search(query):
        return False
    return any(_same_words(wanted, _tokens(part)) for part in _CITATION_PARTS.split(query))


_YEAR_IN_CITATION = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
_CITATION_PARTS = re.compile(r"[,;()\[\]\"“”]")


def _same_words(wanted: set[str], part: set[str]) -> bool:
    shared = len(wanted & part)
    return bool(part) and shared / len(wanted) >= MIN_TITLE_OVERLAP and shared / len(part) >= MIN_TITLE_OVERLAP


def _first(values: Any) -> str:
    if isinstance(values, list):
        values = values[0] if values else ""
    return str(values or "").strip()


def _year(value: Any) -> str:
    match = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str(value or ""))
    return match.group(1) if match else ""


def join_authors(names: list[str]) -> str:
    """McGill: up to three authors joined with commas and "&", more is "et al"."""
    names = [name.strip() for name in names if name and name.strip()]
    if len(names) > 3:
        return names[0] + " et al"
    if len(names) == 3:
        return f"{names[0]}, {names[1]} & {names[2]}"
    return " & ".join(names)


def _full_title(title: str, subtitle: str) -> str:
    title, subtitle = (title or "").strip(), (subtitle or "").strip()
    return f"{title}: {subtitle}" if subtitle and subtitle.casefold() not in title.casefold() else title


# ── Open Library ──────────────────────────────────────────────────────


def search_books(query: str, limit: int = 3) -> list[dict]:
    """Book candidates for a free-text title (optionally with author)."""
    response = request_with_retry(openlibrary_session, "GET", "https://openlibrary.org/search.json",
                                  params={"q": query, "limit": 6, "fields": _OL_FIELDS}, read_timeout=12)
    response.raise_for_status()
    candidates = []
    for doc in response.json().get("docs") or []:
        editions = ((doc.get("editions") or {}).get("docs") or [])
        edition = editions[0] if editions else {}
        olid = str(edition.get("key") or "").rsplit("/", 1)[-1]
        title = doc.get("title") or ""
        if not re.fullmatch(r"OL\d+M", olid) or not title_matches(title, query):
            continue
        authors = join_authors(doc.get("author_name") or [])
        publisher, year = _first(edition.get("publisher")), _year(_first(edition.get("publish_date")))
        where = ", ".join(part for part in (publisher, year) if part)
        display = _full_title(edition.get("title") or title, edition.get("subtitle") or "")
        candidates.append({
            "display": "书 · " + display + (f" — {authors}" if authors else "") + (f" ({where})" if where else ""),
            "name": display, "verified": True, "bib_provider": "openlibrary", "olid": olid,
        })
        if len(candidates) >= limit:
            break
    return candidates


def fetch_edition(olid: str) -> dict | None:
    """The full Open Library record for one edition (authors, places, publishers)."""
    from local_tools.openlibrary_api import fetch_edition_data
    if not re.fullmatch(r"OL\d+M", olid or ""):
        return None
    return fetch_edition_data("books/" + olid)


_MINOR_WORDS = {"a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet", "of", "on", "in", "at",
                "to", "by", "as", "with", "from", "into", "over", "per", "via", "v", "vs"}


def english_title_case(title: str) -> str:
    """Library catalogues store "The concept of law"; McGill titles English works in title case.

    Only ever raises a first letter: words the catalogue already capitalized
    (names, acronyms, "iPhone") are left exactly as recorded.
    """
    words = title.split(" ")
    out = []
    for index, word in enumerate(words):
        starts_clause = index == 0 or out[-1].endswith((":", "?", "!", "—", "–"))
        core = re.sub(r"^\W+|\W+$", "", word).casefold()
        raise_it = starts_clause or index == len(words) - 1 or core not in _MINOR_WORDS
        if raise_it:
            # Per hyphen part; a part with a capital anywhere ("iPhone") is left alone.
            word = "-".join(part[:1].upper() + part[1:] if part[:1].islower() and part[1:] == part[1:].lower()
                            else part for part in word.split("-"))
        out.append(word)
    return " ".join(out)


def book_values(record: dict) -> dict[str, str]:
    """Template field values copied from an Open Library ``jscmd=data`` record."""
    place = _first([p.get("name") for p in record.get("publish_places") or [] if isinstance(p, dict)])
    # Open Library brackets uncertain places ("[S.l.]", "[New York?]").
    place = re.sub(r"^\[(.*)\]$", r"\1", place).replace("?", "").strip()
    if place.casefold().rstrip(".") in {"s.l", "sl", ""}:
        place = ""
    title = _full_title(record.get("title") or "", record.get("subtitle") or "")
    if "eng" in (record.get("languages") or []):
        # Only when the catalogue says English: French titles keep sentence case.
        title = english_title_case(title)
    return {
        "author": join_authors([a.get("name", "") for a in record.get("authors") or [] if isinstance(a, dict)]),
        "title": title,
        "edition": str(record.get("edition_name") or "").strip(),
        "place": place,
        "publisher": _first([p.get("name") for p in record.get("publishers") or [] if isinstance(p, dict)]),
        "year": _year(record.get("publish_date")),
    }


# ── Crossref ──────────────────────────────────────────────────────────


def search_articles(query: str, limit: int = 3) -> list[dict]:
    """Journal-article candidates for a free-text title (optionally with author)."""
    response = request_with_retry(
        crossref_session, "GET", "https://api.crossref.org/works", headers=_CROSSREF_HEADERS,
        params={"query.bibliographic": query, "rows": 6, "filter": "type:journal-article",
                "select": "DOI,title,author,container-title,published"}, read_timeout=12)
    response.raise_for_status()
    candidates = []
    for item in (response.json().get("message") or {}).get("items") or []:
        title, doi = _first(item.get("title")), str(item.get("DOI") or "").strip()
        if not doi.startswith("10.") or not title_matches(title, query):
            continue
        authors = join_authors([author_name(a) for a in item.get("author") or []])
        journal, year = _first(item.get("container-title")), published_year(item)
        where = " ".join(part for part in (journal, f"({year})" if year else "") if part)
        candidates.append({
            "display": f"文章 · “{title}”" + (f" — {authors}" if authors else "") + (f", {where}" if where else ""),
            "name": title, "verified": True, "bib_provider": "crossref", "doi": doi,
        })
        if len(candidates) >= limit:
            break
    return candidates


def author_name(author: Any) -> str:
    if not isinstance(author, dict):
        return ""
    given, family = str(author.get("given") or "").strip(), str(author.get("family") or "").strip()
    return f"{given} {family}".strip() or str(author.get("name") or "").strip()


def published_year(record: dict) -> str:
    for key in ("published-print", "published", "issued", "published-online"):
        parts = (record.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return ""


def article_values(record: dict) -> dict[str, str]:
    """Template field values copied from a Crossref ``works/{doi}`` message."""
    page = str(record.get("page") or "").strip()
    return {
        "author": join_authors([author_name(a) for a in record.get("author") or []]),
        "title": _first(record.get("title")),
        "year": published_year(record),
        "volume": str(record.get("volume") or "").strip(),
        "issue": str(record.get("issue") or "").strip(),
        "journal": _first(record.get("container-title")),
        "first_page": re.split(r"[-–]", page)[0].strip() if page else "",
    }


# ── Both catalogues at once ───────────────────────────────────────────

