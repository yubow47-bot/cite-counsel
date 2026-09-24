"""Books from Open Library, as records.

An ISBN lookup returns one record; a title search returns edition
candidates, each with its own OLID as the source id. Fields are copied
from the catalogue record; whatever the record lacks (a place of
publication, say) stays missing -- write it with ``record__add_field``
(copied from a database record, or the user's words), and the citation is
then honest about its provenance.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.source_tools import source_book_title, source_isbn
from harness.plugin import Plugin, Result, Tool

ISBN_FACTS = (r"\bISBN(?:-1[03])?\b", r"\b97[89][- ]?\d{1,5}[- ]?\d+[- ]?\d+[- ]?[\dxX]\b")


class IsbnParams(BaseModel):
    isbn: str = Field(min_length=10, max_length=20, description="An ISBN-10 or ISBN-13")


def find_isbn(ctx, p: IsbnParams) -> Result:
    return _result(ctx, source_isbn(p.isbn))


class BookParams(BaseModel):
    title: str = Field(min_length=3, max_length=300,
                       description="The book title (optionally with author), as the user wrote it")


def find_book(ctx, p: BookParams) -> Result:
    return _result(ctx, source_book_title(p.title))


def _result(ctx, records):
    refs = [ctx.save(r) for r in records]
    blocks = [{"type": "record_card", "ref": ref, "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()}, "verified": True}
              for ref, r in zip(refs, records)]
    return Result({"found": len(records), "records": [{"ref": ref} for ref in refs],
                   "note": "records are stored; refer to them by ref" if records else "no match"},
                  blocks, final=bool(records))


PLUGIN = Plugin(
    name="openlibrary",
    title="书籍（Open Library）",
    description="Look up a book by ISBN or by title; fields come from the library catalogue record.",
    instructions="Records you receive are numbered (rec_N). Refer to them by number. For a title search, show "
                 "the candidates and let the user pick. If a field is missing, write it with "
                 "record__add_field -- copy it from a record on screen, or ask the user; a copied database "
                 "value keeps the record verified, anything else stays unverified, which is correct.",
    tools=[Tool("isbn", "Fetch a book's catalogue record by ISBN.", IsbnParams, find_isbn),
           Tool("book", "Search books by title.", BookParams, find_book)],
    category="source",
    fact_patterns=ISBN_FACTS,
    default_enabled=True,
)