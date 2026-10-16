"""Historical executions remain auditable without poisoning a fresh recheck."""
import json

import pytest

from core import inquiry, research
from core.tool_contracts import Artifact, Derivation, Field, Record
from harness.records import Store


SCOPE = {"jurisdiction": "Fixture", "as_of": "2026-10-15", "procedure": "notice",
         "factual_context": "The source rule is being checked."}


def save(store, artifact, previous=None):
    return store.put(artifact, supersedes=previous)


def source(store, text, previous=None):
    return save(store, Record("document", {"text": Field(text, "extracted")}, "fixture", "rule"), previous)


def receipt(store, identifier, ref=None, status="ok"):
    return {"id": identifier, "tool": "web__fetch", "category": "extract", "status": status,
            "executed": True, "refs": [{"ref": ref, "fingerprint": store.fingerprint(ref)}] if ref else [],
            "error_kind": "timeout" if status == "error" else None, "coverage": None}


def pursue(store, inquiry_ref, receipts, identifiers, *, task="q1", status="resolved"):
    return save(store, inquiry.pursue(store, inquiry_ref, task, status=status,
                receipt_ids=identifiers, receipts=receipts, plan="Check the current rule."), inquiry_ref)


def claim(store, source_ref, previous=None):
    record = store.get_exact(source_ref, Record)
    text = record.fields["text"].value
    excerpt = {"schema": research.SCHEMA, "type": "excerpt", "quote": text, "speaker": "author",
               "source": {"ref": source_ref, "fingerprint": store.fingerprint(source_ref),
                          "work": {"provider": "fixture", "record_id": "rule", "source_type": "document"},
                          "field": "text", "start": 0, "end": len(text), "field_sha256": research.digest(text),
                          "quote_sha256": research.digest(text), "origin": "extracted", "source_id": None},
               "dependencies": [research.dependency(store, source_ref, "source")]}
    excerpt_ref = save(store, Artifact("legal_research", json.dumps(excerpt), Derivation((Field(text, "model"),), "fixture")))
    node = {"schema": research.SCHEMA, "type": "claim", "text": text, "status": "supported",
            "basis_kind": "source", "assessment": "model_assessed", "premises": [],
            "evidence": [{"excerpt_ref": excerpt_ref, "relation": "supports", "note": "Direct source wording."}],
            "scope": {**SCOPE, "applicability": "asserted_applicable"},
            "dependencies": [research.dependency(store, excerpt_ref, "supports")]}
    return save(store, Artifact("legal_research", json.dumps(node), Derivation((Field(text, "model"),), "fixture")), previous)


def codes(report):
    return {item["code"] for item in report["issues"]}


@pytest.fixture
def work():
    store = Store()
    old = source(store, "Notice requires two weeks.")
    first = receipt(store, "first", old)
    current = save(store, inquiry.frame(store, "What notice is required?", scope=SCOPE))
    current = pursue(store, current, [first], ["first"])
    new = source(store, "Notice requires four weeks.", old)
    second = receipt(store, "second", new)
    return store, old, new, [first, second], current


def test_complete_argument_recheck_removes_only_the_obsolete_task_dependency(work):
    store, old, new, receipts, current = work
    old_claim = claim(store, old)
    current = save(store, inquiry.assess(store, current, old_claim, "The old rule's direct wording.",
                   verdict="supported", response="No additional condition is asserted.", scope=SCOPE,
                   receipt_ids=["first"], receipts=receipts, claim_kind="direct", premise_ids=[]), current)
    assert inquiry.inspect(store, current, receipts)["stale"]

    new_claim = claim(store, new, old_claim)
    current = save(store, inquiry.refine(store, current, candidate_refs=[new_claim]), current)
    current = save(store, inquiry.assess(store, current, new_claim, "The current rule's direct wording.",
                   verdict="supported", response="No additional condition is asserted.", scope=SCOPE,
                   receipt_ids=["second"], receipts=receipts, claim_kind="direct", premise_ids=[]), current)
    current = pursue(store, current, receipts, ["second"])
    current = save(store, inquiry.answer(store, current, "Four weeks under the retrieved rule.", claim_refs=[new_claim]), current)

    report = inquiry.inspect(store, current, receipts)
    assert report["readiness"] == "ready_for_review" and not report["stale"]
    assert report["affected_claim_refs"] == []
    assert old not in {value["ref"] for value in report["dependencies"]}
    assert new in {value["ref"] for value in report["dependencies"]}
    task = report["tasks"][0]
    assert [value["id"] for value in task["receipts"]] == ["first", "second"]
    assert task["current_receipt_ids"] == ["second"] and len(task["attempts"]) == 2


def test_plan_only_update_does_not_erase_an_obsolete_evidence_binding(work):
    store, old, _, receipts, current = work
    current = pursue(store, current, receipts, [], status="investigating")
    report = inquiry.inspect(store, current, receipts)
    assert report["stale"] and "dependency_revised" in codes(report)
    assert old in {value["ref"] for value in report["dependencies"]}
    assert report["tasks"][0]["execution"] == "not_executed"
    assert report["tasks"][0]["current_receipt_ids"] == ["first"]


@pytest.mark.parametrize("status,expected", [("error", "source_execution_failed"), ("no_match", "search_no_match")])
def test_plan_only_update_keeps_the_last_attempts_failure_or_no_match(work, status, expected):
    store, _, _, receipts, current = work
    receipts.append(receipt(store, "failed", status=status))
    current = pursue(store, current, receipts, ["failed"], status="blocked")
    current = pursue(store, current, receipts, [], status="investigating")
    report = inquiry.inspect(store, current, receipts)
    assert expected in codes(report)
    assert report["tasks"][0]["execution"] == "not_executed"


@pytest.mark.parametrize("corrupt_history", [False, True])
def test_unavailable_or_changed_history_is_audited_without_blocking_fresh_receipts(work, corrupt_history):
    store, _, _, receipts, current = work
    current = pursue(store, current, receipts, ["second"])
    table = [{**receipts[0], "refs": []}, receipts[1]] if corrupt_history else [receipts[1]]
    report = inquiry.inspect(store, current, table)
    assert not report["stale"] and report["mechanical_binding"] == "intact"
    warning = next(item for item in report["issues"] if item["code"] == "historical_receipt_unavailable_or_changed")
    assert not warning["blocking"] and warning["receipt_id"] == "first"
    assert "receipt_unavailable_or_changed" not in codes(report)
    assert report["tasks"][0]["execution"] == "material_returned"


def test_current_receipt_integrity_still_blocks_the_current_task(work):
    store, _, _, receipts, current = work
    current = pursue(store, current, receipts, ["second"])
    report = inquiry.inspect(store, current, [receipts[0]])
    assert "receipt_unavailable_or_changed" in codes(report)
    assert report["mechanical_binding"] == "invalid"


def test_another_current_task_still_keeps_its_old_source_dependency(work):
    store, old, _, receipts, current = work
    current = save(store, inquiry.refine(store, current, questions=[{"id": "q2", "question": "Does an exception apply?"}]), current)
    current = pursue(store, current, receipts, ["first"], task="q2")
    current = pursue(store, current, receipts, ["second"])
    report = inquiry.inspect(store, current, receipts)
    assert report["stale"]
    assert old in {value["ref"] for value in report["dependencies"]}
    assert report["tasks"][1]["current_receipt_ids"] == ["first"]


def test_old_saved_snapshot_with_history_dependencies_is_inspected_compatibly(work):
    store, old, _, receipts, current = work
    current = pursue(store, current, receipts, ["second"])
    node = inquiry.unpack(store.get_exact(current))
    node["tasks"][0].pop("current_receipt_ids")
    node["dependencies"].append(research.dependency(store, old, "investigation_output"))
    legacy = save(store, inquiry.pack(node), current)
    report = inquiry.inspect(store, legacy, receipts)
    assert not report["stale"]
    assert old not in {value["ref"] for value in report["dependencies"]}
    assert old in {value["ref"] for value in report["object"]["dependencies"]}  # Original audit snapshot survives.


def test_attempt_log_truncation_does_not_reactivate_all_historical_sources(work):
    store, old, new, receipts, current = work
    current = pursue(store, current, receipts, ["second"])
    for _ in range(45):
        current = pursue(store, current, receipts, [], status="investigating")
    report = inquiry.inspect(store, current, receipts)
    assert len(report["tasks"][0]["attempts"]) == 40
    assert report["tasks"][0]["current_receipt_ids"] == ["second"]
    assert not report["stale"]
    assert {value["ref"] for value in report["dependencies"]} == {new}
    assert old in {value["ref"] for receipt in report["tasks"][0]["receipts"] for value in receipt["refs"]}

