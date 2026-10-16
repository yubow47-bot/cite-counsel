"""Plugin-level investigation behavior, persistence, visible checks and export."""

import json

import pytest

from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from harness.session import SessionStore
from plugins import inquiry as plugin
from plugins import research


SCOPE = {"jurisdiction": "Synthetic Province", "as_of": "2026-10-15",
         "procedure": "administrative review", "factual_context": "paper notice; receipt disputed"}


@pytest.fixture
def ctx(tmp_path):
    h = Harness(discover(), model="offline", config_path=tmp_path / "config.json")
    session, token = h.sessions.start()
    session._test_token = token
    return h.context(session, "inquiry")


def source_and_claim(ctx):
    text = "Paper notice is effective on receipt; dispatch alone does not establish receipt."
    source = ctx.records.put(Record("legislation", {"full_text": Field(text, "database", source_id="synthetic:notice")},
                                    "synthetic", "notice-rule"))
    rc = ctx._harness.context(ctx.session, "research")
    ex = research.excerpt(rc, research.ExcerptParams(ref=source, start=0, end=len(text), quote=text)).content["ref"]
    claim = research.claim(rc, research.ClaimParams(text="Paper notice requires receipt, rather than dispatch alone.",
                                                   status="supported", evidence=[{"excerpt_ref": ex, "relation": "supports"}],
                                                   scope={**SCOPE, "applicability": "asserted_applicable"})).content["ref"]
    ctx.session.tool_receipts.append({"id": "exec-source-1", "tool": "synthetic__fetch", "category": "source", "status": "ok",
                                      "refs": [{"ref": source, "fingerprint": ctx.records.fingerprint(source)}],
                                      "sequence": 1, "coverage": {"complete": True}, "error_kind": None})
    return source, claim


def completed(ctx):
    source, claim = source_and_claim(ctx)
    ref = plugin.frame(ctx, plugin.FrameParams(question="Was sending the paper notice enough?", scope=SCOPE,
                                               candidate_refs=[claim])).content["ref"]
    ref = plugin.pursue(ctx, plugin.PursueParams(ref=ref, task_id="q1", status="resolved", receipt_ids=["exec-source-1"],
                                                 findings="The actual retrieved rule distinguishes dispatch from receipt.")).content["ref"]
    ref = plugin.assess(ctx, plugin.AssessParams(ref=ref, claim_ref=claim, rationale="The text expressly limits effectiveness to receipt.", claim_kind="direct",
                                                 verdict="supported", scope=SCOPE, receipt_ids=["exec-source-1"],
                                                 response="Checked the full retrieved rule for an exception to paper notice; none was present in this synthetic text.")).content["ref"]
    ref = plugin.answer(ctx, plugin.AnswerParams(ref=ref, text="Dispatch alone does not establish effective notice; establish receipt.",
                                                 claim_refs=[claim])).content["ref"]
    return source, claim, ref


def test_frame_is_retained_and_exposes_missing_information_not_a_fake_answer(ctx):
    result = plugin.frame(ctx, plugin.FrameParams(question="Can I challenge this decision?",
                                                  questions=[{"question": "Which process and date apply?", "why": "This determines the route."}]))
    report = result.content
    assert ctx.session.retained and ctx.state["active_ref"] == report["ref"]
    assert report["object"]["answer"] is None and report["readiness"] == "open"
    assert report["object"]["scope"] == dict.fromkeys(SCOPE)
    assert report["next_steps"] and not report["legal_verification"]
    assert "Can I challenge this decision?" in plugin.context_provider(ctx)
    assert plugin.final_response_checks(ctx, "Which process applies?") == []


def test_plans_and_unsupported_receipts_cannot_close_a_question(ctx):
    ref = plugin.frame(ctx, plugin.FrameParams(question="What does the rule require?")).content["ref"]
    planned = plugin.pursue(ctx, plugin.PursueParams(ref=ref, task_id="q1", plan="Find the official current rule.")).content
    assert planned["tasks"][0]["execution"] == "not_executed"
    with pytest.raises(ValueError, match="trusted tool receipts"):
        plugin.pursue(ctx, plugin.PursueParams(ref=planned["ref"], task_id="q1", status="resolved", findings="I checked everything."))
    with pytest.raises(ValueError, match="Unknown trusted"):
        plugin.pursue(ctx, plugin.PursueParams(ref=planned["ref"], task_id="q1", receipt_ids=["invented-search"]))
    assert ctx.records.current_ref(planned["ref"]) == planned["ref"]


def test_actual_source_receipts_are_visible_but_internal_writing_is_not_investigation(ctx):
    _, claim = source_and_claim(ctx)
    ctx.session.tool_receipts.append({"id": "exec-claim", "tool": "research__claim", "category": "function", "status": "ok",
                                      "refs": [{"ref": claim, "fingerprint": ctx.records.fingerprint(claim)}], "sequence": 2})
    report = plugin.frame(ctx, plugin.FrameParams(question="Check this rule.", candidate_refs=[claim])).content
    assert [row["id"] for row in report["recent_receipts"]] == ["exec-source-1"]
    result = plugin.pursue(ctx, plugin.PursueParams(ref=report["ref"], task_id="q1", status="resolved", receipt_ids=["exec-claim"])).content
    assert result["tasks"][0]["execution"] == "not_executed"
    assert "investigation_not_executed" in {issue["code"] for issue in result["blockers"]}


def test_latest_inquiry_directory_does_not_duplicate_every_revision(ctx):
    _, _, ref = completed(ctx)
    data = plugin.list_objects(ctx, plugin.ListParams(query="paper notice")).content
    assert data["total"] == 1 and data["items"][0]["ref"] == ref
    assert data["items"][0]["readiness"] == "conditional"
    report = plugin.inspect_object(ctx, plugin.InspectParams()).content
    assert report["assessments"][0]["direct_basis"] == "model_interpretation"
    assert report["assessments"][0]["effective_verdict"] == "limited"
    assert not plugin.inspect_object(ctx, plugin.InspectParams()).content["legal_verification"]


def test_fresh_export_reports_source_changes_and_keeps_original_evidence(ctx):
    source, _, ref = completed(ctx)
    before = plugin.export_action(ctx, {"ref": ref}).blocks[0]
    assert before["readiness"] == "conditional"
    ctx.records.put(Record("legislation", {"full_text": Field("A revised rule changes effectiveness.", "database", source_id="synthetic:notice")},
                           "synthetic", "notice-rule"), supersedes=source)
    after = plugin.export_action(ctx, {"ref": ref}).blocks[0]
    assert after["readiness"] == "open" and "claim_stale" in after["markdown"]
    assert "dispatch alone does not establish receipt" in after["markdown"]
    assert "not a guarantee of legal or factual correctness" in after["markdown"]
    assert "review did not complete" in plugin.inspect_object(ctx, plugin.InspectParams(ref=ref)).content["object"]["answer"]["text"]


def test_final_checks_follow_current_work_without_warning_on_an_unrelated_next_turn(ctx):
    _, claim, ref = completed(ctx)
    answer = plugin.answer(ctx, plugin.AnswerParams(ref=ref, text="The answer depends on proving receipt.", claim_refs=[claim],
                                                 gaps=["Actual receipt has not been established."]))
    checks = plugin.final_response_checks(ctx, answer.reply)
    assert checks[0]["readiness"] == "open" and checks[0]["items"]
    ctx.session.timeline.append({"role": "user", "text": "Change the topic: hello."})
    assert plugin.final_response_checks(ctx, "Hello.") == []


def test_inquiry_and_trusted_execution_bindings_survive_disk_restore(ctx):
    _, _, ref = completed(ctx)
    h = ctx._harness
    h.sessions.save(ctx.session)
    restored = SessionStore(store_dir=h.sessions.store_dir).get(ctx.session.id, ctx.session._test_token)
    assert restored is not None and restored.retained
    rc = h.context(restored, "inquiry")
    report = plugin.inspect_object(rc, plugin.InspectParams()).content
    assert report["ref"] == ref and report["readiness"] == "conditional"
    assert report["tasks"][0]["trusted_receipt_count"] == 1
    assert report["recent_receipts"][0]["id"] == "exec-source-1"
    assert json.loads(restored.records.get_exact(ref).content)["answer"]["claim_refs"]


def test_clarification_refines_scope_without_losing_investigation_history(ctx):
    _, _, ref = completed(ctx)
    changed = plugin.frame(ctx, plugin.FrameParams(prior_ref=ref, scope={"factual_context": "electronic notice, receipt unknown"},
                                                    questions=[{"id": "q2", "question": "Does an electronic-service exception apply?"}])).content
    node = changed["object"]
    assert node["scope"]["jurisdiction"] == SCOPE["jurisdiction"]
    assert node["scope"]["factual_context"] == "electronic notice, receipt unknown"
    assert node["tasks"][0]["attempts"] and node["tasks"][1]["id"] == "q2"
    assert node["answer"]["needs_refresh"]
    assert changed["readiness"] == "open" and ctx.records.current_ref(ref) == changed["ref"]
    with pytest.raises(ValueError, match="revision conflict"):
        plugin.frame(ctx, plugin.FrameParams(prior_ref=ref, goal="An obsolete edit."))


def test_context_keeps_critical_state_and_valid_json_with_long_questions(ctx):
    plugin.frame(ctx, plugin.FrameParams(question="Is this legal? " * 130, goal="An important decision. " * 70,
                                         scope={"factual_context": "Unknown facts. " * 130},
                                         questions=[{"question": "Missing fact? " * 65} for _ in range(10)]))
    text = plugin.context_provider(ctx)
    summary = json.loads(text)
    assert len(text) <= 2200 and summary["readiness"] == "open"
    assert summary["blockers"] and summary["blocker_count"] >= 10


def test_unavailable_active_snapshot_does_not_silently_hide_final_check(ctx):
    _, _, ref = completed(ctx)
    ctx.records._objects.pop(ref)  # synthetic damaged archive / missing snapshot
    checks = plugin.final_response_checks(ctx, "A final answer.")
    assert checks[0]["readiness"] == "open"
    assert checks[0]["items"][0]["code"] == "inquiry_unavailable"

