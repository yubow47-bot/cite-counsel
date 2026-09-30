"""Journal articles from Crossref, as records.

A DOI lookup returns one record; a title search returns candidates, each
with its own DOI as the source id. The catalogue search is fuzzy and
always returns something, so results are kept only when the record's own
title is essentially contained in what the user typed -- and the user
still chooses between the survivors.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.source_tools import source_article_title, source_doi
from harness.plugin import Plugin, Result, Tool

DOI_FACTS = (r"\b10\.\d{4,9}/\S+",)


class DoiParams(BaseModel):
    doi: str = Field(min_length=7, max_length=300, description="A DOI, e.g. 10.1016/j.ijinfomgt.2020.102188")


def find_doi(ctx, p: DoiParams) -> Result:
    return _result(ctx, source_doi(p.doi))


class ArticleParams(BaseModel):
    title: str = Field(min_length=3, max_length=300,
                       description="The article title (optionally with author), as the user wrote it")


def find_article(ctx, p: ArticleParams) -> Result:
    return _result(ctx, source_article_title(p.title))


def _result(ctx, records):
    refs = [ctx.save(r) for r in records]
    blocks = [{"type": "record_card", "ref": ref, "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()}, "verified": True}
              for ref, r in zip(refs, records)]
    return Result({"found": len(records), "records": [ctx.records.summary(ref) for ref in refs],
                   "note": "records are stored; refer to them by ref" if records else "no match"},
                  blocks, final=bool(records))


PLUGIN = Plugin(
    name="crossref",
    title="Journal articles (Crossref)",
    description="Look up a journal article by DOI or by title; fields come from the Crossref record.",
    tools=[Tool("doi", "Fetch a journal article's metadata by DOI.", DoiParams, find_doi),
           Tool("article", "Search journal articles by title.", ArticleParams, find_article)],
    category="source",
    fact_patterns=DOI_FACTS,
    default_enabled=True,
)