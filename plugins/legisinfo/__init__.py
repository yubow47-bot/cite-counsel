"""Federal bills from LEGISinfo, as records.

Fields are copied from the LEGISinfo record with a ``legisinfo:…``
source id. The keyword search filters the current Parliament sessions'
bills by their titles; it is a search over the same database, not an
interpretation of what a bill does.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.source_tools import source_bill_keyword, source_bills
from local_tools.source_status import SourceRequestError
from harness.plugin import Plugin, Result, Tool

BILL_FACTS = (r"\b[CS]-\d+\b",)


class BillParams(BaseModel):
    number: str = Field(min_length=2, max_length=20,
                        description="A bill number such as C-22 or S-5, optionally with its year")


def find_bill(ctx, p: BillParams) -> Result:
    try:
        return _result(ctx, source_bills(p.number))
    except SourceRequestError as exc:
        return Result(exc.content(), [{"type": "notice", "level": "warning", "text": str(exc)}])


class BillsParams(BaseModel):
    keywords: str = Field(min_length=2, max_length=300,
                          description="Keywords from the bill's title, in English or French")


def search_bills(ctx, p: BillsParams) -> Result:
    try:
        return _result(ctx, source_bill_keyword(p.keywords))
    except SourceRequestError as exc:
        return Result(exc.content(), [{"type": "notice", "level": "warning", "text": str(exc)}])


def _result(ctx, records):
    coverage = getattr(records, "coverage", {"status": "complete", "source": "legisinfo"})
    refs = [ctx.save(r, {"source_coverage": coverage}) for r in records]
    blocks = [{"type": "record_card", "ref": ref, "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()}, "verified": True}
              for ref, r in zip(refs, records)]
    incomplete = coverage.get("status") != "complete"
    if incomplete:
        blocks.append({"type": "notice", "level": "warning",
                       "text": "Search coverage is incomplete or cached data is stale; check coverage before relying on the result."})
    return Result({"found": len(records), "records": [ctx.records.summary(ref) for ref in refs], "coverage": coverage,
                   "note": "search incomplete; inspect coverage and retry failed scopes" if incomplete else
                           "records are stored; refer to them by ref" if records else "no match in the requested sessions"},
                  blocks, final=bool(records))


PLUGIN = Plugin(
    name="legisinfo",
    title="Federal bills (LEGISinfo)",
    description="Look up federal bills by number or by keywords in the title, with their status and dates.",
    tools=[Tool("bill", "Find a federal bill by its number.", BillParams, find_bill),
           Tool("bills", "Find federal bills by keywords in the title, newest first.", BillsParams,
                search_bills)],
    category="source",
    fact_patterns=BILL_FACTS,
    default_enabled=True,
)
