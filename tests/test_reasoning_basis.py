"""Saved reasoning preserves uncertainty rather than laundering it as proof."""

import json

import pytest

from core import research
from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.core import Harness
from plugins import research as plugin


@pytest.fixture
def ctx(tmp_path):
    harness = Harness({"research": plugin.PLUGIN}, model="offline", config_path=tmp_path / "config.json")
    session, _ = harness.sessions.start()
    return harness.context(session, "research")


def assertion(ctx, text, **values):
    return plugin.claim(ctx, plugin.ClaimParams(text=text, **values)).content


def excerpt(ctx, text, speaker="unknown"):
    ref = ctx.records.put(Record("case", {"full_text": Field(text, "database", source_id="synthetic:notice")},
                                 "synthetic", "notice"))
    return plugin.excerpt(ctx, plugin.ExcerptParams(ref=ref, start=0, end=len(text), quote=text,
                                                    speaker=speaker)).content["ref"]


def codes(report, ref=None):
    return {issue["code"] for issue in report["issues"] if ref is None or issue["ref"] == ref}


def test_default_source_assertion_retains_support_gap_and_unverified_semantics(ctx):
    report = assertion(ctx, "A source-based claim", status="supported", assessment="model_assessed")
    assert report["object"]["basis_kind"] == "source"
    assert "support_gap" in codes(report)
    assert report["assertions"][0]["basis_verification"] == "asserted_only"
    assert report["legal_verification"] is False
    assert report["assertions"][0]["semantic_verification"] is False


def test_inference_uses_saved_premises_and_reasoning_without_requiring_redundant_direct_quote(ctx):
    ex = excerpt(ctx, "The court required proof of delivery.", speaker="court")
    premise = assertion(ctx, "Delivery must be proved", kind="premise", status="supported",
                        evidence=[{"excerpt_ref": ex, "relation": "supports", "note": "Rule stated for this procedure"}])["ref"]
    report = assertion(ctx, "The available record does not yet establish the requirement", kind="conclusion",
                       basis_kind="inference", status="supported", premises=[premise],
                       reasoning="The rule requires delivery; the supplied record contains no delivery evidence.",
                       limitations=["Further evidence may establish delivery."])
    assert "support_gap" not in codes(report, report["ref"])
    assert "inference_gap" not in codes(report)
    view = report["assertions"][0]
    assert view["premises"] == [premise] and view["reasoning"].startswith("The rule")
    assert view["limitations"] == ["Further evidence may establish delivery."]
    assert report["mechanical_binding"] == "intact" and not report["stale"]
    assert report["legal_verification"] is False


@pytest.mark.parametrize("premises,reasoning,expected", [
    (False, "Because of the rule", ["missing_premises"]),
    (True, "  ", ["missing_reasoning"]),
    (False, "", ["missing_premises", "missing_reasoning"]),
])
def test_incomplete_inference_is_saved_with_specific_gap(ctx, premises, reasoning, expected):
    prior = assertion(ctx, "Unverified premise", kind="premise", basis_kind="assumption")["ref"]
    report = assertion(ctx, "Tentative conclusion", basis_kind="inference", status="supported",
                       premises=[prior] if premises else [], reasoning=reasoning)
    issue = next(issue for issue in report["issues"] if issue["ref"] == report["ref"] and issue["code"] == "inference_gap")
    assert issue["missing"] == expected
    assert "support_gap" not in codes(report, report["ref"])
    assert report["object"]["legal_verification"] is False


def test_supported_assumption_remains_explicitly_unverified_after_user_acknowledgement(ctx):
    report = assertion(ctx, "The notice reached the tenant", kind="premise", basis_kind="assumption",
                       status="supported", assessment="model_assessed")
    ref = report["ref"]
    assert "unverified_assumption" in codes(report) and "support_gap" not in codes(report)
    plugin.review_action(ctx, {"ref": ref})
    reviewed = research.inspect(ctx.records, ref, ctx.state["reviews"])
    assert reviewed["review_status"] == "user_reviewed"
    assert "unverified_assumption" in codes(reviewed)
    assert reviewed["unresolved_bases"][0]["path"] == [ref]
    assert reviewed["legal_verification"] is False


def test_uncertain_premise_paths_survive_nested_reasoning_and_draft(ctx):
    premise = assertion(ctx, "The notice was delivered", kind="premise", basis_kind="assumption", status="disputed")["ref"]
    middle = assertion(ctx, "The notice may be effective", kind="premise", basis_kind="inference", status="supported",
                       premises=[premise], reasoning="Effectiveness depends on the disputed delivery premise.")["ref"]
    conclusion = assertion(ctx, "An outcome depends on effective notice", kind="conclusion", basis_kind="inference",
                           status="supported", premises=[middle], reasoning="The outcome depends on the intermediate premise.")["ref"]
    report = plugin.draft(ctx, plugin.DraftParams(title="Conditional advice", claim_refs=[conclusion])).content
    issue = next(item for item in report["unresolved_bases"] if item["code"] == "unresolved_disputed")
    assert issue["path"] == [report["ref"], conclusion, middle, premise]
    assert all("support_gap" not in codes(report, ref) for ref in (premise, middle, conclusion))
    assert report["legal_verification"] is False


def test_scope_evidence_notes_speaker_and_counterargument_are_visible_without_certification(ctx):
    ex = excerpt(ctx, "The tenant alleged that the notice never arrived.", speaker="party")
    report = assertion(ctx, "Delivery remains contested", status="disputed", basis_kind="source",
                       evidence=[{"excerpt_ref": ex, "relation": "opposes", "note": "An allegation, not a finding of nondelivery"}],
                       scope={"jurisdiction": "Ontario", "as_of": "2026-10-15", "procedure": "Notice review",
                              "factual_context": "The account concerns this tenancy only", "applicability": "conditional",
                              "note": "Depends on the notice type and service method"},
                       reasoning="This account conflicts with the claimed delivery.",
                       counterargument="The sender may have a signed receipt.",
                       counterargument_response="No receipt has yet been supplied; obtain it before resolving delivery.",
                       limitations=["The party's account is not independently confirmed."], assessment="model_assessed")
    view = report["assertions"][0]
    assert view["scope"]["factual_context"] == "The account concerns this tenancy only"
    assert view["scope"]["applicability"] == "conditional"
    assert view["scope_verification"] == "asserted_only"
    assert view["evidence"][0]["note"].startswith("An allegation")
    assert view["evidence"][0]["relationship_verification"] == "asserted_only"
    assert report["sources"][0]["speaker"] == "party"
    assert report["sources"][0]["speaker_verification"] == "asserted_only"
    assert "opposing_evidence" in codes(report)
    assert "unhandled_counterevidence" not in codes(report)
    assert report["legal_verification"] is False


@pytest.mark.parametrize("with_evidence", [False, True])
def test_unanswered_counterargument_or_opposing_evidence_stays_actionable(ctx, with_evidence):
    evidence = [{"excerpt_ref": excerpt(ctx, "There was no delivery."), "relation": "opposes"}] if with_evidence else []
    report = assertion(ctx, "Delivery occurred", status="supported", basis_kind="assumption", evidence=evidence,
                       counterargument="A material alternative explanation" if not with_evidence else "")
    assert "unhandled_counterevidence" in codes(report)
    assert not report["stale"]


def test_unknown_scope_is_preserved_and_explicit_nonapplicability_is_reported(ctx):
    unknown = assertion(ctx, "Working fact", basis_kind="assumption")
    assert all(unknown["assertions"][0]["scope"][name] == "" for name in research.SCOPE_FIELDS)
    assert unknown["assertions"][0]["scope"]["applicability"] == "unknown"
    assert "scope_not_applicable" not in codes(unknown)
    outside = assertion(ctx, "Rule from a different procedure", scope={"applicability": "not_applicable"})
    assert "scope_not_applicable" in codes(outside)
    assert outside["legal_verification"] is False


def test_old_saved_assertion_without_basis_fields_retains_source_semantics(ctx):
    legacy = {"schema": research.SCHEMA, "type": "claim", "text": "Legacy claim", "status": "supported",
              "evidence": [], "premises": [], "assessment": "model_assessed", "dependencies": []}
    ref = ctx.records.put(Artifact("legal_research", json.dumps(legacy), Derivation((Field("Legacy claim", "model"),), "legacy")))
    report = research.inspect(ctx.records, ref)
    assert "support_gap" in codes(report)
    assert report["assertions"][0]["basis_kind"] == "source"
    assert report["assertions"][0]["scope"]["applicability"] == "unknown"


def test_export_carries_exact_nested_basis_scope_counterargument_and_unresolved_paths(ctx):
    premise = assertion(ctx, "The notice was received", kind="premise", basis_kind="assumption", status="unknown")["ref"]
    conclusion = assertion(ctx, "The deadline may have started", kind="conclusion", basis_kind="inference", status="supported",
                           premises=[premise], reasoning="Receipt may trigger the deadline under the applicable rule.",
                           scope={"jurisdiction": "Ontario", "procedure": "Synthetic deadline inquiry", "applicability": "conditional"},
                           counterargument="A different trigger may apply", counterargument_response="Check the actual rule first",
                           limitations=["No applicable rule has been obtained yet"])["ref"]
    draft = plugin.draft(ctx, plugin.DraftParams(title="Conditional answer", claim_refs=[conclusion])).content["ref"]
    exported = plugin.export_action(ctx, {"ref": draft}).blocks[0]["markdown"]
    assert "UNVERIFIED ASSUMPTION" in exported
    assert "Basis: inference (asserted only)" in exported
    assert "jurisdiction: Ontario" in exported and "as_of: unknown" in exported
    assert "Receipt may trigger the deadline" in exported
    assert "Counterargument (asserted): A different trigger" in exported
    assert "Response to counterargument/evidence (asserted): Check the actual rule" in exported
    assert f"{draft} → {conclusion} → {premise}" in exported
    assert "Legal verification: false" in exported
