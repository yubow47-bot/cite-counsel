"""Actual webpage changes invalidate only that source's saved arguments."""
from unittest.mock import patch

import pytest

from core import research
from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from plugins import research as research_plugin, web


@pytest.fixture
def ctx(tmp_path):
    harness = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    return harness.context(session, "web")


def fetch(ctx, text, *, url="https://example.com/rule", final_url=None, body=None, title="Rule"):
    body = body if body is not None else ("<html>" + text + "</html>").encode()

    def extract(requested, capture=None):
        capture(body, {"final_url": final_url or requested, "status": 200,
                       "content_type": "text/html", "etag": "", "last_modified": ""})
        return {"page_title": title, "raw_text": text}

    with patch("local_tools.web_extract.extract_from_url", side_effect=extract):
        return web.fetch(ctx, web.FetchParams(url=url))


def ref(result):
    return result.content["record"]["ref"]


def source_body(ctx, source_ref):
    return (ctx._harness.sessions.store_dir / ctx.session.id / "sources" / f"{source_ref}.body").read_bytes()


def saved_claim(ctx, source_ref, quote):
    rc = ctx._harness.context(ctx.session, "research")
    excerpt = research_plugin.excerpt(rc, research_plugin.ExcerptParams(ref=source_ref, quote=quote)).content["ref"]
    return research_plugin.claim(rc, research_plugin.ClaimParams(
        text=quote, evidence=[{"excerpt_ref": excerpt, "relation": "supports"}])).content["ref"]


def test_changed_body_invalidates_its_claim_and_preserves_exact_versions(ctx):
    original = fetch(ctx, "Notice requires two weeks.")
    source = ref(original)
    claim = saved_claim(ctx, source, "Notice requires two weeks.")
    other = fetch(ctx, "A separate rule.", url="https://example.com/other")
    other_claim = saved_claim(ctx, ref(other), "A separate rule.")

    updated = fetch(ctx, "Notice requires four weeks.")

    assert ctx.records.current_ref(source) == ref(updated)
    assert ctx.records.get_exact(source).fields["text"].value == "Notice requires two weeks."
    assert source_body(ctx, source) == b"<html>Notice requires two weeks.</html>"
    assert source_body(ctx, ref(updated)) == b"<html>Notice requires four weeks.</html>"
    report = research.inspect(ctx.records, claim)
    assert report["stale"]
    assert any(issue["code"] == "dependency_revised" and issue["ref"] == source for issue in report["issues"])
    assert not research.inspect(ctx.records, other_claim)["stale"]


def test_same_text_reuses_material_but_retains_each_actual_response(ctx):
    first = fetch(ctx, "The same rule.", body=b"<html>first layout</html>")
    material_ref = ref(first)
    claim = saved_claim(ctx, material_ref, "The same rule.")
    repeated = fetch(ctx, "The same rule.", body=b"<html>second layout</html>", title="Updated heading")
    retrieval_ref = repeated.content["source_snapshot"]["ref"]

    assert ref(repeated) == material_ref
    assert retrieval_ref != material_ref
    assert repeated.blocks[0]["ref"] == retrieval_ref  # Saved response opens this fetch.
    assert source_body(ctx, material_ref) == b"<html>first layout</html>"
    assert source_body(ctx, retrieval_ref) == b"<html>second layout</html>"
    assert ctx.records.meta(retrieval_ref)["web_source"]["material_ref"] == material_ref
    assert "text" not in ctx.records.get_exact(retrieval_ref).fields
    assert not research.inspect(ctx.records, claim)["stale"]

    changed = fetch(ctx, "A changed rule.")
    assert ctx.records.current_ref(material_ref) == ref(changed)
    assert research.inspect(ctx.records, claim)["stale"]


@pytest.mark.parametrize("url", [
    "https://example.com/rule?version=2",
    "https://example.com/Rule",
    "https://example.com/rule/",
    "https://different.example/rule",
    "http://example.com/rule",
])
def test_different_actual_pages_are_not_merged_by_title_or_text(ctx, url):
    first = fetch(ctx, "Original text.")
    second = fetch(ctx, "Different text.", url=url)
    assert ctx.records.current_ref(ref(first)) == ref(first)
    assert ref(second) != ref(first)


def test_only_safe_url_normalization_coalesces_a_source(ctx):
    first = fetch(ctx, "First version.", url="HTTPS://EXAMPLE.COM:443/rule#one")
    second = fetch(ctx, "Second version.", url="https://example.com/rule#two")
    assert ctx.records.current_ref(ref(first)) == ref(second)
    a = fetch(ctx, "First query.", url="https://example.com/query?a=1&b=2")
    fetch(ctx, "Other query order.", url="https://example.com/query?b=2&a=1")
    assert ctx.records.current_ref(ref(a)) == ref(a)


def test_response_destination_identifies_redirected_material(ctx):
    first = fetch(ctx, "First version.", url="https://example.com/old",
                  final_url="https://example.com/current#part")
    second = fetch(ctx, "Second version.", url="https://example.com/current")
    assert ctx.records.current_ref(ref(first)) == ref(second)

    unrelated = fetch(ctx, "Unrelated destination.", url="https://example.com/old",
                      final_url="https://example.com/another")
    assert ctx.records.current_ref(ref(second)) == ref(second)
    assert ref(unrelated) != ref(second)


def test_legacy_records_use_response_url_or_request_id_and_all_changed_heads_advance(ctx):
    old = ctx.save(Record("website", {"text": Field("Earlier copy.", "extracted")},
                          "web", "https://example.com/old"),
                   meta={"source_snapshot": {"final_url": "https://example.com/rule#part"}})
    duplicate = ctx.save(Record("website", {"text": Field("Another saved copy.", "extracted")},
                                "web", "https://example.com/rule"))
    updated = fetch(ctx, "Current text.")
    assert ctx.records.current_ref(old) != old
    assert ctx.records.current_ref(duplicate) != duplicate
    assert ctx.records.get(old).fields["text"].value == "Current text."
    assert ctx.records.get(duplicate).fields["text"].value == "Current text."
    latest = fetch(ctx, "Newest text.")
    assert ctx.records.get(old).fields["text"].value == "Newest text."
    assert ctx.records.get(duplicate).fields["text"].value == "Newest text."
    assert ref(latest) != ref(updated)


def test_composed_or_model_origin_material_is_not_a_source_version(ctx):
    composed = ctx.save(Record("website", {"text": Field("Edited text.", "extracted")},
                               "web", "https://example.com/rule"), meta={"composed": True})
    fetch(ctx, "Actual text.")
    assert ctx.records.current_ref(composed) == composed


def test_repeated_actual_fetch_issues_a_receipt_for_the_reused_material(ctx):
    from harness.receipts import issue

    first = fetch(ctx, "Same text.")
    repeated = fetch(ctx, "Same text.")
    receipt = issue(ctx.session, ctx._harness.plugins, "web__fetch", repeated.content)
    assert receipt["status"] == "ok" and receipt["executed"]
    assert ref(first) in receipt["source_refs"]
    assert repeated.content["source_snapshot"]["ref"] in receipt["source_refs"]

