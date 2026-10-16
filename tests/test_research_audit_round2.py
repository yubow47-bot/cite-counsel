"""Second-pass workflow regressions: revisions and portable inspection exports."""

import json

import pytest

from core.tool_contracts import Artifact, Field, Record
from harness.core import Harness, _model_content
from plugins import research as plugin


@pytest.fixture
def ctx(tmp_path):
    h = Harness({"research": plugin.PLUGIN}, model="offline", config_path=tmp_path / "config.json")
    session, _ = h.sessions.start()
    return h.context(session, "research")


def source(ctx, text="The application is dismissed.", supersedes=None):
    return ctx.records.put(Record("case", {"full_text": Field(text, "database", source_id="synthetic:alpha")},
                                  "synthetic", "alpha"), supersedes=supersedes)


def excerpt(ctx, ref):
    text = ctx.records.get_exact(ref, Record).fields["full_text"].value
    return plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, start=0, end=len(text), quote=text)).content["ref"]


def claim(ctx, text, **values):
    return plugin.claim(ctx, plugin.ClaimParams(text=text, **values)).content["ref"]


def test_stale_revision_conflicts_without_hiding_or_reparenting_latest_work(ctx):
    first = claim(ctx, "Original premise", kind="premise")
    corrected = claim(ctx, "Corrected premise", kind="premise", prior_ref=first)
    count = len(ctx.records.all())
    with pytest.raises(ValueError, match="revision conflict.*" + corrected):
        claim(ctx, "Stale edit", kind="premise", prior_ref=first)
    assert len(ctx.records.all()) == count
    assert ctx.records.current_ref(first) == corrected
    assert ctx.records.meta(corrected)["supersedes"] == first


@pytest.mark.parametrize("indirect", [False, True])
def test_revision_rejects_direct_and_transitive_dependency_on_its_previous_version(ctx, indirect):
    first = claim(ctx, "Original premise", kind="premise")
    dependency = claim(ctx, "Dependent premise", kind="premise", premises=[first]) if indirect else first
    count = len(ctx.records.all())
    with pytest.raises(ValueError, match="depend on the version it replaces"):
        claim(ctx, "Revised premise", kind="premise", prior_ref=first, premises=[dependency])
    assert len(ctx.records.all()) == count
    assert ctx.records.current_ref(first) == first


def test_revision_rejects_dependency_on_an_earlier_version_in_same_lineage(ctx):
    first = claim(ctx, "Original premise", kind="premise")
    second = claim(ctx, "Second premise", kind="premise", prior_ref=first)
    with pytest.raises(ValueError, match="depend on the version it replaces"):
        claim(ctx, "Third premise", kind="premise", prior_ref=second, premises=[first])
    assert ctx.records.current_ref(first) == second


def test_draft_stale_prior_is_also_a_conflict(ctx):
    cl = claim(ctx, "A claim")
    first = plugin.draft(ctx, plugin.DraftParams(title="First", claim_refs=[cl])).content["ref"]
    second = plugin.draft(ctx, plugin.DraftParams(title="Second", claim_refs=[cl], prior_ref=first)).content["ref"]
    with pytest.raises(ValueError, match="revision conflict"):
        plugin.draft(ctx, plugin.DraftParams(title="Stale draft", claim_refs=[cl], prior_ref=first))
    assert ctx.records.current_ref(first) == second


def test_nested_premise_evidence_survives_in_draft_and_export_with_assertion_labels(ctx):
    src = source(ctx)
    ex = excerpt(ctx, src)
    premise = claim(ctx, "Important disputed premise", kind="premise", status="disputed",
                    evidence=[{"excerpt_ref": ex, "relation": "supports", "note": "Limited to this application"}])
    middle = claim(ctx, "Intermediate premise", kind="premise", premises=[premise])
    conclusion = claim(ctx, "Conclusion from premises", kind="conclusion", premises=[middle])
    draft = plugin.draft(ctx, plugin.DraftParams(title="Memo", claim_refs=[conclusion])).content["ref"]
    saved = json.loads(ctx.records.get_exact(draft, Artifact).content)["markdown"]
    exported = plugin.export_action(ctx, {"ref": draft}).blocks[0]["markdown"]
    for text in (saved, exported):
        assert "The application is dismissed." in text
        assert src + ".full_text[0:" in text
        assert "Important disputed premise" in text
        assert "State: disputed (asserted)" in text
        assert "supports (asserted)" in text
        assert "Speaker: unknown (asserted only)" in text
        assert "Source-version fingerprint:" in text
    assert "unresolved_disputed" in exported
    assert "Legal verification: false" in exported


def test_export_rechecks_old_cards_and_preserves_original_draft_snapshot(ctx):
    src = source(ctx)
    ex = excerpt(ctx, src)
    cl = claim(ctx, "Application dismissed", status="supported", evidence=[{"excerpt_ref": ex, "relation": "supports"}])
    draft = plugin.draft(ctx, plugin.DraftParams(title="Memo", claim_refs=[cl])).content["ref"]
    plugin.review_action(ctx, {"ref": draft})
    original = ctx.records.get_exact(draft, Artifact).content
    new = source(ctx, "The application is allowed.", supersedes=src)
    result = plugin.export_action(ctx, {"ref": draft})
    block = result.blocks[0]
    assert block["stale"] and block["review_status"] == "review_invalidated"
    assert "Generated at:" in block["markdown"] and "Exact object: " + draft in block["markdown"]
    assert "STALE" in block["markdown"] and "dependency_revised" in block["markdown"]
    assert "current " + new in block["markdown"]
    assert "The application is dismissed." in block["markdown"]
    assert ctx.records.get_exact(draft, Artifact).content == original


def test_export_of_superseded_draft_reports_its_current_version(ctx):
    cl = claim(ctx, "Claim")
    old = plugin.draft(ctx, plugin.DraftParams(title="Original", claim_refs=[cl])).content["ref"]
    new = plugin.draft(ctx, plugin.DraftParams(title="Revised", claim_refs=[cl], prior_ref=old)).content["ref"]
    export = plugin.export_action(ctx, {"ref": old}).blocks[0]
    assert export["stale"] and export["current_ref"] == new
    assert "Current version: " + new in export["markdown"]
    assert "object_revised" in export["markdown"]


def test_long_inspection_keeps_review_flags_ahead_of_document_preview(ctx):
    src = source(ctx, "Long passage. " * 550)
    ex = excerpt(ctx, src)
    report = plugin.inspect_object(ctx, plugin.InspectParams(ref=ex)).content
    preview = _model_content(report)
    assert '"truncated": true' in preview
    summary = json.loads(preview)
    assert summary["review_status"] == "pending"
    assert summary["mechanical_binding"] == "intact"
    assert summary["legal_verification"] is False


def test_export_action_is_served_by_harness_without_mutating_saved_research(ctx):
    cl = claim(ctx, "Claim")
    draft = plugin.draft(ctx, plugin.DraftParams(title="Memo", claim_refs=[cl])).content["ref"]
    before = ctx.records.fingerprint(draft)
    history, timeline, loaded = list(ctx.session.messages), list(ctx.session.timeline), list(ctx.session.loaded)
    blocks = ctx._harness.run_action(ctx.session, "research", "export", {"ref": draft})
    assert blocks[0]["type"] == "research_export" and blocks[0]["filename"].endswith(".md")
    assert ctx.records.fingerprint(draft) == before
    assert ctx.session.messages == history and ctx.session.timeline == timeline and ctx.session.loaded == loaded
    assert "review" not in {tool.name for tool in plugin.PLUGIN.tools}


def test_research_directory_pages_and_searches_older_objects(ctx):
    oldest = claim(ctx, "The oldest searchable assertion")
    for index in range(34):
        claim(ctx, f"Newer assertion {index}")
    first = plugin.list_objects(ctx, plugin.ListParams()).content
    assert first["total"] == 35 and len(first["items"]) == 30 and first["next_offset"] == 30
    second = plugin.list_objects(ctx, plugin.ListParams(offset=first["next_offset"])).content
    assert second["offset"] == 30 and len(second["items"]) == 5 and second["next_offset"] is None
    assert second["items"][-1]["ref"] == oldest
    found = plugin.list_objects(ctx, plugin.ListParams(query="oldest")).content
    assert found["query"] == "oldest" and found["total"] == 1 and found["items"][0]["ref"] == oldest


def test_excerpt_default_selects_text_document_field_and_binds_actual_name(ctx):
    text = "The decisive passage in an uploaded document."
    ref = ctx.records.put(Record("document", {"text": Field(text, "extracted", source_id="file:synthetic")},
                                 "file", "synthetic-document"))
    result = plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, start=0, end=len(text), quote=text)).content
    assert result["object"]["source"]["field"] == "text"
    assert result["sources"][0]["field"] == "text"
    assert result["mechanical_binding"] == "intact"
    assert not result["stale"]


def test_excerpt_explicit_missing_field_reports_available_names_without_fallback(ctx):
    text = "A passage."
    ref = ctx.records.put(Record("document", {"text": Field(text, "extracted")}, "file", "synthetic"))
    count = len(ctx.records.all())
    with pytest.raises(ValueError, match="Available fields: text"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, field="full_text", start=0, end=len(text), quote=text))
    assert len(ctx.records.all()) == count
