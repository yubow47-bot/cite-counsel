"""Cross-layer regressions for honest investigation execution and retention."""

import json

from core.tool_contracts import Field, Record
from harness import receipts
from harness.core import Harness, TOOL_CONTENT_LIMIT, _model_content
from harness.records import Store
from harness.session import SessionStore


def call(harness, session, name, arguments):
    return harness._execute_once(session, {"id": "audit", "function": {
        "name": name, "arguments": json.dumps(arguments)}}, {})[1]


def test_empty_read_cannot_stand_in_for_inspection_of_a_real_source(tmp_path):
    harness = Harness({}, config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    ref = session.records.put(Record("document", {"text": Field("A short passage.", "database", source_id="fixture:a")},
                                      "fixture", "a"))
    result = call(harness, session, "record__read", {"ref": ref, "field": "text", "offset": 9000})
    assert result.content["text"] == ""
    assert session.tool_receipts[-1]["category"] != "read"


def test_only_the_actual_field_read_can_establish_source_inspection(tmp_path):
    harness = Harness({}, config_path=tmp_path / "h.json")
    session, _ = harness.sessions.start()
    ref = session.records.put(Record("document", {
        "title": Field("A sourced title", "database", source_id="fixture:a"),
        "text": Field("A generated account", "model"),
        "original": Field("The original passage", "extracted", source_id="fixture:a")}, "fixture", "a"))
    call(harness, session, "record__read", {"ref": ref, "field": "text"})
    assert session.tool_receipts[-1]["category"] == "core"
    call(harness, session, "record__read", {"ref": ref, "field": "original", "offset": 4, "limit": 8})
    receipt = session.tool_receipts[-1]
    assert receipt["category"] == "read"
    assert receipt["refs"] == [{"ref": ref, "fingerprint": session.records.fingerprint(ref)}]


def test_record_field_values_cannot_add_unrelated_refs_to_execution_receipt():
    store = Store()
    actual = store.put(Record("document", {"text": Field("Actual output", "database", source_id="fixture:a")}, "fixture", "a"))
    unrelated = store.put(Record("document", {"text": Field("Other work", "database", source_id="fixture:b")}, "fixture", "b"))
    content = {"records": [{"ref": actual, "fields": {"ref": unrelated, "source_ref": unrelated,
                                                         "text": '{"ref":"' + unrelated + '"}'}}]}
    assert receipts.referenced_objects(content, store) == [{"ref": actual, "fingerprint": store.fingerprint(actual)}]


def test_large_inquiry_preview_preserves_open_state_and_receipt_handle():
    value = {"ref": "art_250", "current_ref": "art_251", "object": {"question": "Long user context. " * 3000},
             "readiness": "open", "stale": True, "legal_verification": False,
             "mechanical_binding": "invalid", "blockers": [{"code": "source_execution_failed", "detail": "Failure. " * 500}],
             "next_steps": [{"next_step": "A necessary check. " * 500}],
             "issues": [{"code": "claim_stale", "detail": "Old evidence. " * 500}],
             "_execution_receipt": {"id": "run_audit123", "tool": "fixture__search", "category": "source",
                                    "status": "error", "coverage": {"complete": False}}}
    rendered = _model_content(value)
    preview = json.loads(rendered)
    assert len(rendered) <= TOOL_CONTENT_LIMIT
    assert preview["readiness"] == "open" and preview["stale"] is True
    assert preview["legal_verification"] is False and preview["mechanical_binding"] == "invalid"
    assert preview["blockers"] and preview["issues"]
    assert preview["_execution_receipt"]["id"] == "run_audit123"
    assert preview["_execution_receipt"]["status"] == "error"


def test_retained_investigation_keeps_attachments_and_events_past_expiry_and_disk_cap(tmp_path):
    root = tmp_path / "sessions"
    store = SessionStore(store_dir=root, limit=1)
    session, token = store.start()
    session.retained = True
    attachment = store.attachment_dir(session) / "material.txt"
    attachment.write_text("Synthetic retained material", encoding="utf-8")
    session.attachments["attachment-1"] = {"path": str(attachment), "name": "material.txt"}
    store.append_event(session, "tool_result", {"fixture": True})
    store.save(session)
    from session_db import set_wall_expires
    set_wall_expires(store, session.id, 0)
    # Both stale age and a newer unretained cache entry must leave retained
    # work intact, including the associated directory and execution log.
    store.start()
    revived = SessionStore(store_dir=root, limit=1).get(session.id, token)
    assert revived is not None and revived.retained
    assert attachment.read_text(encoding="utf-8") == "Synthetic retained material"
    assert (root / (session.id + ".events.jsonl")).is_file()
    assert revived.attachments["attachment-1"]["name"] == "material.txt"
