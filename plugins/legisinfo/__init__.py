"""Federal bills from LEGISinfo, as records.

Fields are copied from the LEGISinfo record with a ``legisinfo:…``
source id. The keyword search filters the current Parliament sessions'
bills by their titles; it is a search over the same database, not an
interpretation of what a bill does.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.grounding import grounded_reply_guard
from core.source_tools import source_bill_keyword, source_bills
from harness.plugin import Plugin, Result, Tool

BILL_FACTS = (r"\b[CS]-\d+\b",)


class BillParams(BaseModel):
    number: str = Field(min_length=2, max_length=20,
                        description="A bill number such as C-22 or S-5, optionally with its year")


def find_bill(ctx, p: BillParams) -> Result:
    return _result(ctx, source_bills(p.number))


class BillsParams(BaseModel):
    keywords: str = Field(min_length=2, max_length=300,
                          description="Keywords from the bill's title, in English or French")


def search_bills(ctx, p: BillsParams) -> Result:
    return _result(ctx, source_bill_keyword(p.keywords))


def _result(ctx, records):
    blocks = [{"type": "record_card", "ref": ctx.records.put(r), "source_type": r.source_type,
               "fields": {k: f.value for k, f in r.fields.items()}, "verified": True}
              for r in records]
    return Result({"found": len(records), "records": [{"ref": b["ref"]} for b in blocks],
                   "note": "records are stored; refer to them by ref" if records else "no match"},
                  blocks, final=bool(records))


PLUGIN = Plugin(
    name="legisinfo",
    title="联邦议案（LEGISinfo）",
    description="Look up federal bills by number or by keywords in the title, with their status and dates.",
    instructions="Records you receive are numbered (rec_N). Refer to them by number; the card shows the title "
                 "and status. Say what was searched when nothing matches.",
    tools=[Tool("bill", "Find a federal bill by its number.", BillParams, find_bill),
           Tool("bills", "Find federal bills by keywords in the title, newest first.", BillsParams,
                search_bills)],
    category="source",
    fact_patterns=BILL_FACTS,
    default_enabled=True,
    reply_guard=grounded_reply_guard,
)