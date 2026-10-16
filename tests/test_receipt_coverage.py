"""Which receipts show that an exact passage was obtained or read."""
from core import receipt_coverage as rc
from core.research import digest

TEXT = "0123456789" * 10


def read(identifier, left, right, *, text=TEXT, ref="rec_1", field="text"):
    return {"id": identifier, "category": "read", "status": "ok",
            "refs": [{"ref": ref, "fingerprint": "f1"}],
            "observations": [{"ref": ref, "field": field, "offset": left, "end": right,
                              "text_sha256": digest(text[left:right])}]}


def test_bound_to_requires_success_execution_category_and_exact_version():
    rows = [read("ok", 0, 10),
            {**read("failed", 0, 10), "status": "error"},
            {**read("not_run", 0, 10), "executed": False},
            {**read("old", 0, 10), "refs": [{"ref": "rec_1", "fingerprint": "f0"}]},
            {"id": "fetch", "category": "source", "status": "ok", "refs": [{"ref": "rec_1", "fingerprint": "f1"}]},
            {"id": "draft", "category": "function", "status": "ok", "refs": [{"ref": "rec_1", "fingerprint": "f1"}]}]
    found = rc.bound_to(rows, "rec_1", "f1", {"source", "extract", "read"})
    assert [row["id"] for row in found] == ["ok", "fetch"]


def test_adjacent_reads_jointly_cover_a_passage():
    matches = [read("a", 0, 30), read("b", 30, 60)]
    spans = rc.read_spans(matches, "rec_1", "text", TEXT, 20, 50)
    assert rc.covering_reads(spans, 20, 50) == ["a", "b"]


def test_gap_or_hash_mismatch_does_not_cover():
    assert rc.covering_reads(rc.read_spans([read("a", 0, 30), read("b", 40, 60)], "rec_1", "text", TEXT, 20, 50), 20, 50) is None
    tampered = read("x", 0, 60, text="different text entirely" * 5)
    assert rc.read_spans([tampered], "rec_1", "text", TEXT, 20, 50) == []


def test_later_unrelated_read_does_not_displace_covering_read():
    matches = [read("covering", 10, 50), read("elsewhere", 80, 100)]
    spans = rc.read_spans(matches, "rec_1", "text", TEXT, 20, 40)
    assert rc.covering_reads(spans, 20, 40) == ["covering"]


def test_other_field_or_record_is_ignored():
    assert rc.read_spans([read("a", 0, 60, field="title")], "rec_1", "text", TEXT, 0, 10) == []
    assert rc.read_spans([read("a", 0, 60, ref="rec_2")], "rec_1", "text", TEXT, 0, 10) == []
