"""A named decisive source is checked by execution, not a model's assertion."""
import copy
import json
from types import SimpleNamespace

import pytest

from core.tool_contracts import Field, Record
from harness.receipts import issue
from harness.records import Store
from plugins.inquiry.source_checks import missing_required, normalize_url, required_urls, source_report


@pytest.fixture
def session():
    return SimpleNamespace(records=Store(), tool_receipts=[], messages=[])


def source(session, url, *, text="The actual rule.", origin="extracted", metadata=None):
    return session.records.put(Record("website", {"url": Field(url, origin), "text": Field(text, origin)},
                                      "web", url), meta=metadata)


def fetched(session, ref, *, extra=None):
    return issue(session, {"web": SimpleNamespace(category="extract")}, "web__fetch",
                 {"record": {"ref": ref}, **(extra or {})})


def report(session, *urls, premises=()):
    return source_report(session.records, session.tool_receipts, user_texts=urls,
                         premises=premises, messages=session.messages)


def test_explicit_current_question_urls_and_next_checks_are_deduplicated():
    assert required_urls(user_texts=["Read [rule](HTTPS://EXAMPLE.COM:443/Rule?q=2#one). Then https://example.com/rule。"],
                         premises=[{"next_check": "Check https://example.com/Rule?q=2#two and https://example.com/other"}]) == [
        "https://example.com/Rule?q=2", "https://example.com/rule", "https://example.com/other"]
    assert required_urls(user_texts=["New question without a URL"]) == []
    assert required_urls(user_texts=["[Article](https://example.com/Act_(2025))."]) == ["https://example.com/Act_(2025)"]


@pytest.mark.parametrize("url", ["https://example.com/Rule", "https://example.com/rule/", "http://example.com/rule",
                                 "https://example.com/rule?v=2", "https://example.com/rule?a=1&b=2"])
def test_different_paths_queries_and_schemes_are_not_satisfied_by_another_page(session, url):
    fetched(session, source(session, "https://example.com/rule"))
    assert report(session, url)[0]["status"] == "unvisited"
    assert normalize_url("https://example.com/rule?a=1&b=2") != normalize_url("https://example.com/rule?b=2&a=1")


def test_receipt_binds_the_original_record_and_actual_redirect_urls(session):
    ref = source(session, "https://example.com/old", metadata={"source_snapshot": {
        "requested_url": "https://example.com/old", "final_url": "https://EXAMPLE.com:443/new#section"}})
    receipt = fetched(session, ref)
    rows = report(session, "https://example.com/old https://example.com/new")
    assert all(row == {"url": url, "status": "retrieved", "refs": [ref], "receipt_ids": [receipt["id"]], "binding": "acquired"}
               for url, row in zip(["https://example.com/old", "https://example.com/new"], rows))
    assert missing_required(rows) == []


@pytest.mark.parametrize("invalid", ["no_receipt", "not_executed", "wrong_fingerprint", "model_body", "composed", "search_only"])
def test_narrative_or_unbound_material_cannot_complete_required_source(session, invalid):
    url = "https://example.com/rule"
    ref = source(session, url, origin="model" if invalid == "model_body" else "extracted",
                 metadata={"composed": True} if invalid == "composed" else None)
    receipt = fetched(session, ref)
    if invalid == "no_receipt":
        session.tool_receipts.clear()
    elif invalid == "not_executed":
        receipt["executed"] = False
    elif invalid == "wrong_fingerprint":
        receipt["refs"][0]["fingerprint"] = "made-up"
    elif invalid == "search_only":
        receipt["refs"] = []
    session.messages = [{"role": "assistant", "content": f"I read and verified {url}."}]
    rows = report(session, url)
    assert rows[0]["status"] == "unvisited"
    assert missing_required(rows) == rows


def test_valid_body_read_works_but_metadata_only_read_does_not(session):
    url = "https://example.com/rule"
    ref = source(session, url)
    issue(session, {}, "record__read", {"ref": ref, "field": "url", "origin": "extracted", "offset": 0, "text": url})
    assert report(session, url)[0]["status"] == "unvisited"
    receipt = issue(session, {}, "record__read", {"ref": ref, "field": "text", "origin": "extracted", "offset": 0,
                                                   "text": "The actual rule."})
    assert report(session, url)[0]["receipt_ids"] == [receipt["id"]]
    assert report(session, url)[0]["binding"] == "read"
    receipt["observations"][0]["text_sha256"] = "wrong"
    assert report(session, url)[0]["status"] == "unvisited"


def test_same_material_retrieval_binds_new_request_alias_without_fabricating_text(session):
    original = source(session, "https://example.com/current")
    audit = session.records.put(Record("web_fetch", {"url": Field("https://example.com/alias", "extracted")},
                                       "web", "https://example.com/alias"),
                                meta={"web_source": {"material_ref": original}})
    fetched(session, original, extra={"source_snapshot": {"ref": audit}})
    assert report(session, "https://example.com/alias")[0]["refs"] == [original]
    # An audit-only ref cannot vouch for material omitted from that execution.
    session.tool_receipts.clear()
    fetched(session, audit)
    assert report(session, "https://example.com/alias")[0]["status"] == "unvisited"


def failure(session, url, *, kind="tool_value_error", embedded_id=None):
    receipt = issue(session, {"web": SimpleNamespace(category="extract")}, "web__fetch",
                    {"error": "Page is blocked.", "kind": kind})
    call_id = f"c{len(session.tool_receipts)}"
    session.messages.extend([
        {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": "web__fetch", "arguments": json.dumps({"url": url})}}]},
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"error": "Page is blocked.",
            "_execution_receipt": {"id": embedded_id or receipt["id"]}})},
    ])
    return receipt


def test_executed_failure_is_reported_honestly_and_remains_missing(session):
    url = "https://example.com/blocked"
    receipt = failure(session, url)
    rows = report(session, url)
    assert rows == [{"url": url, "status": "failed", "refs": [], "receipt_ids": [receipt["id"]], "binding": None}]
    assert missing_required(rows) == rows
    fetched(session, source(session, url))
    assert report(session, url)[0]["status"] == "retrieved"


def test_failure_after_valid_current_material_does_not_erase_it(session):
    url = "https://example.com/rule"
    ref = source(session, url)
    fetched(session, ref)
    failed = failure(session, url)
    row = report(session, url)[0]
    assert row["status"] == "retrieved" and row["refs"] == [ref]
    assert failed["id"] in row["receipt_ids"]


def test_superseded_material_is_not_reported_as_currently_retrieved(session):
    url = "https://example.com/rule"
    old = source(session, url)
    fetched(session, old)
    new = session.records.put(Record("website", {"text": Field("Updated rule.", "extracted"),
                                                 "url": Field(url, "extracted")}, "web", url), supersedes=old)
    assert report(session, url)[0]["status"] == "unvisited"
    fetched(session, new)
    assert report(session, url)[0]["refs"] == [new]


@pytest.mark.parametrize("kind,embedded", [("invalid_arguments", None), ("repeated_call", None),
                                           ("tool_value_error", "invented_receipt")])
def test_unexecuted_or_fabricated_failed_call_does_not_count(session, kind, embedded):
    url = "https://example.com/blocked"
    failure(session, url, kind=kind, embedded_id=embedded)
    assert report(session, url)[0]["status"] == "unvisited"


def test_report_is_deterministic_and_does_not_mutate_state(session):
    url = "https://example.com/rule"
    fetched(session, source(session, url))
    before = copy.deepcopy((session.tool_receipts, session.messages))
    first = report(session, url, premises=[{"next_check": "https://example.com/not-read"}])
    assert first == report(session, url, premises=[{"next_check": "https://example.com/not-read"}])
    assert (session.tool_receipts, session.messages) == before
