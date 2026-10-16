"""Research preserves exact sources and propagates real workflow changes."""

import json

import pytest
from pydantic import ValidationError

from core.tool_contracts import Artifact, Field, Record, decode, encode
from harness.core import Harness
from harness.plugin import discover
from harness.session import SessionStore
from plugins import research as plugin


@pytest.fixture
def ctx(tmp_path):
    h = Harness(discover(), model="offline", config_path=tmp_path / "config.json")
    session, _ = h.sessions.start()
    return h.context(session, "research")


def source(ctx, text="[1] The application is dismissed.", work="alpha", supersedes=None):
    return ctx.records.put(Record("case", {"full_text": Field(text, "database", source_id="test:" + work)},
                                  "synthetic", work), supersedes=supersedes)


def excerpt(ctx, ref, quote="The application is dismissed."):
    value = ctx.records.get_exact(ref, Record).fields["full_text"].value
    return plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, start=value.index(quote),
                                                    end=value.index(quote) + len(quote), quote=quote)).content["ref"]


def assertion(ctx, ex, **kwargs):
    values = {"text": "The application was dismissed.", "status": "supported",
              "evidence": [{"excerpt_ref": ex, "relation": "supports"}], **kwargs}
    return plugin.claim(ctx, plugin.ClaimParams(**values)).content["ref"]


def inspect(ctx, ref):
    return plugin.inspect_object(ctx, plugin.InspectParams(ref=ref)).content


def test_same_quote_in_different_works_cannot_change_saved_binding(ctx):
    a, b = source(ctx), source(ctx, work="beta")
    ex = excerpt(ctx, a)
    report = inspect(ctx, ex)
    assert report["sources"][0]["ref"] == a
    assert report["sources"][0]["work"]["record_id"] == "alpha"
    assert report["sources"][0]["fingerprint"] != ctx.records.fingerprint(b)
    assert report["mechanical_binding"] == "intact"
    assert report["legal_verification"] is False
    # Even a corrupted imported artifact cannot keep a successful binding
    # verdict when its work identity is switched under an identical quote.
    obj = ctx.records.get_exact(ex, Artifact)
    data = json.loads(obj.content)
    data["source"]["work"]["record_id"] = "beta"
    forged = ctx.records.put(Artifact(obj.kind, json.dumps(data), obj.derivation))
    assert inspect(ctx, forged)["mechanical_binding"] == "invalid"


def test_wrong_quote_span_and_foreign_session_refs_are_rejected(ctx, tmp_path):
    ref = source(ctx)
    with pytest.raises(ValueError, match="exactly match"):
        plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, start=0, end=4, quote="fake"))
    with pytest.raises(ValidationError):
        plugin.ExcerptParams(ref=ref, start=True, end=4, quote="test")
    foreign = SessionStore().start()[0]
    foreign.records.put(Record("case", {"full_text": Field("unshared", "database", source_id="x")}, "p", "foreign"))
    with pytest.raises(ValueError):
        excerpt(ctx, "rec_999")


def test_model_origin_is_not_accepted_as_original_material(ctx):
    ref = ctx.records.put(Record("case", {"full_text": Field("The application is dismissed.", "model")},
                                 "user", "drafted-source"))
    with pytest.raises(ValueError, match="model-origin"):
        excerpt(ctx, ref)


def test_long_dependency_chains_do_not_copy_full_source_into_every_artifact(ctx):
    text = "Background. " * 10000 + "The application is dismissed."
    ex = excerpt(ctx, source(ctx, text))
    prior = assertion(ctx, ex, kind="premise")
    for index in range(25):
        prior = assertion(ctx, ex, kind="premise", text=f"Dependent premise {index}.", premises=[prior])
    draft = plugin.draft(ctx, plugin.DraftParams(title="Deep dependency memo", claim_refs=[prior])).content["ref"]
    report = inspect(ctx, draft)
    assert not report["stale"] and report["sources"][0]["quote"] == "The application is dismissed."
    assert len(json.dumps(encode(ctx.records.get_exact(draft)))) < 10000
    assert all(len(json.dumps(encode(obj))) < 10000 for _, obj in ctx.records.all(Artifact))


def test_source_revision_preserves_old_excerpt_and_invalidates_review_through_draft(ctx):
    old = source(ctx)
    ex = excerpt(ctx, old)
    cl = assertion(ctx, ex)
    dr = plugin.draft(ctx, plugin.DraftParams(title="Synthetic research", claim_refs=[cl])).content["ref"]
    plugin.review_action(ctx, {"ref": dr})
    assert inspect(ctx, dr)["review_status"] == "user_reviewed"
    new = source(ctx, "[1] The application is allowed.", supersedes=old)
    report = inspect(ctx, dr)
    assert report["stale"] and report["review_status"] == "review_invalidated"
    assert any(i["ref"] == old and i.get("current_ref") == new for i in report["issues"])
    assert report["sources"][0]["quote"] == "The application is dismissed."
    assert report["mechanical_binding"] == "intact"  # old snapshot itself remains exact
    with pytest.raises(ValueError, match="Dependencies changed"):
        plugin.review_action(ctx, {"ref": dr})


def test_premise_revision_invalidates_conclusion_and_draft_without_overwriting(ctx):
    ex = excerpt(ctx, source(ctx))
    premise = assertion(ctx, ex, kind="premise", text="The application concerns this client.", status="unknown")
    conclusion = assertion(ctx, ex, kind="conclusion", premises=[premise])
    draft = plugin.draft(ctx, plugin.DraftParams(title="Memo", claim_refs=[conclusion])).content["ref"]
    plugin.review_action(ctx, {"ref": draft})
    changed = assertion(ctx, ex, kind="premise", text="The application concerns another person.", status="disputed", prior_ref=premise)
    assert inspect(ctx, draft)["review_status"] == "review_invalidated"
    assert "another person" not in inspect(ctx, draft)["object"]["markdown"]
    assert ctx.records.current_ref(premise) == changed
    assert inspect(ctx, conclusion)["stale"]


def test_model_assessment_never_becomes_user_review_or_legal_verification(ctx):
    cl = plugin.claim(ctx, plugin.ClaimParams(text="A sweeping unsupported conclusion.", assessment="model_assessed", status="supported")).content["ref"]
    report = inspect(ctx, cl)
    assert report["review_status"] == "pending" and report["needs_review"]
    assert "support_gap" in {i["code"] for i in report["issues"]}
    assert report["legal_verification"] is False
    assert "review" not in {tool.name for tool in plugin.PLUGIN.tools}
    plugin.review_action(ctx, {"ref": cl})
    report = inspect(ctx, cl)
    assert report["review_status"] == "user_reviewed" and report["issues"]
    assert report["legal_verification"] is False


def test_research_and_review_survive_session_disk_roundtrip(ctx, tmp_path):
    ex = excerpt(ctx, source(ctx))
    claim = assertion(ctx, ex)
    draft = plugin.draft(ctx, plugin.DraftParams(title="Persisted memo", claim_refs=[claim])).content["ref"]
    plugin.review_action(ctx, {"ref": draft})
    store = SessionStore(store_dir=tmp_path / "sessions")
    session, token = store.start()
    session.records.restore([(ref, decode(encode(obj)), meta) for ref, obj, meta in ctx.records.entries()])
    session.state = json.loads(json.dumps(ctx.session.state))
    store.save(session)
    revived = SessionStore(store_dir=tmp_path / "sessions").get(session.id, token)
    restored = ctx._harness.context(revived, "research")
    report = inspect(restored, draft)
    assert report["review_status"] == "user_reviewed"
    assert report["sources"][0]["ref"].startswith("rec_")
    assert plugin.list_objects(restored, plugin.ListParams(query="Persisted")).content["total"] == 1
