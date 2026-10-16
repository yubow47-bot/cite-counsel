"""A saved argument must bind the source acquisition or the actual passage read."""
import json

import pytest

from core import inquiry
from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from harness.receipts import issue
from plugins import inquiry as plugin


QUOTE = "Dispatch does not establish receipt."
TEXT = "Introduction without the rule. " + "Unrelated context. " * 30 + QUOTE + " More context. " * 30
START = TEXT.index(QUOTE)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    harness = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "Does dispatch establish receipt?"})
    session.timeline.append({"role": "user", "text": "Does dispatch establish receipt?"})
    session.records.put(Record("document", {
        "text": Field(TEXT, "extracted"), "summary": Field(TEXT, "extracted")}, "fixture", "notice"))
    monkeypatch.setattr(plugin, "review_candidate", lambda ctx, p, contexts, delivery_evidence=None: (
        p, {"status": "skipped", "semantic_verification": False}))
    return harness.context(session, "inquiry")


def read(ctx, offset, limit, field="text"):
    ctx._harness._execute_once(ctx.session, {"function": {
        "name": "record__read", "arguments": json.dumps({
            "ref": "rec_1", "field": field, "offset": offset, "limit": limit})}}, {})
    return ctx.session.tool_receipts[-1]


def conclude(ctx):
    result = plugin.conclude(ctx, plugin.ConcludeParams(calculations=[], 
        answer="Dispatch alone does not establish receipt.", checks=[plugin.KeyCheck(claim_kind="direct",
            conclusion="Receipt is not established by dispatch alone.",
            passages=[plugin.Passage(source_ref="rec_1", field="text", quote=QUOTE)],
            reasoning="The exact source text distinguishes dispatch from receipt.",
            objection="Another rule could establish a dispatch exception.",
            response="The available passage does not establish such an exception.")]))
    return inquiry.inspect(ctx.records, result.content["ref"], ctx.session.tool_receipts)["assessments"][0]["receipts"]


@pytest.mark.parametrize("failure", ["other_span", "other_field", "incomplete", "gap", "hash", "fingerprint", "legacy"])
def test_uncovered_or_invalid_reads_cannot_bind_a_passage_or_save_partial_analysis(ctx, failure):
    if failure == "other_span":
        read(ctx, 0, 20)
    elif failure == "other_field":
        read(ctx, START, len(QUOTE), field="summary")
    elif failure == "incomplete":
        read(ctx, START, len(QUOTE) - 1)
    elif failure == "gap":
        read(ctx, START, 10)
        read(ctx, START + 11, len(QUOTE) - 11)
    else:
        receipt = read(ctx, START, len(QUOTE))
        if failure == "hash":
            receipt["observations"][0]["text_sha256"] = "0" * 64
        elif failure == "fingerprint":
            receipt["refs"][0]["fingerprint"] = "changed-version"
        else:
            receipt.pop("observations")
    before = list(ctx.records.all())
    with pytest.raises(ValueError, match=f"field text, span {START}:"):
        conclude(ctx)
    assert list(ctx.records.all()) == before


def test_recent_unrelated_read_does_not_replace_earlier_exact_passage_inspection(ctx):
    relevant = read(ctx, START - 5, len(QUOTE) + 10)
    read(ctx, 0, 20)
    bound = conclude(ctx)
    assert [row["id"] for row in bound] == [relevant["id"]]
    assert bound[0]["observations"][0]["field"] == "text"


def test_adjacent_reads_jointly_cover_an_excerpt_without_another_read(ctx):
    first = read(ctx, START, 12)
    second = read(ctx, START + 12, len(QUOTE) - 12)
    assert [row["id"] for row in conclude(ctx)] == [first["id"], second["id"]]


def test_search_returned_context_counts_beyond_the_literal_match(ctx):
    ctx._harness._execute_once(ctx.session, {"function": {
        "name": "record__search", "arguments": json.dumps({
            "ref": "rec_1", "field": "text", "query": "Dispatch"})}}, {})
    receipt = ctx.session.tool_receipts[-1]
    assert receipt["observations"][0]["match_end"] < START + len(QUOTE)
    assert [row["id"] for row in conclude(ctx)] == [receipt["id"]]


@pytest.mark.parametrize("name", ["web__fetch", "file__extract"])
def test_acquisition_receipt_remains_acquisition_after_an_unrelated_read(ctx, name):
    # The receipt issuer, not model text, binds the exact saved output version.
    receipt = issue(ctx.session, ctx._harness.plugins, name, {"record": {"ref": "rec_1"}})
    assert receipt["category"] in {"source", "extract"}
    read(ctx, 0, 20)
    bound = conclude(ctx)
    assert [row["id"] for row in bound] == [receipt["id"]]
    assert bound[0]["category"] == receipt["category"]
    assert not bound[0]["observations"]


def test_full_passage_inspection_is_preferred_to_a_newer_acquisition_receipt(ctx):
    inspected = read(ctx, START, len(QUOTE))
    issue(ctx.session, ctx._harness.plugins, "web__fetch", {"record": {"ref": "rec_1"}})
    assert [row["id"] for row in conclude(ctx)] == [inspected["id"]]

