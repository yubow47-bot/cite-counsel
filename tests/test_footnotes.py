"""Footnote order decides full / Ibid / supra; edits are revisioned and idempotent."""
import random
from copy import deepcopy
from itertools import permutations

import pytest

from core import footnotes
from core.tool_contracts import Field, Record
from harness.core import Harness
from harness.plugin import discover
from plugins import footnotes as plugin

CASES = {"gladue": "*R v Gladue*, [1999] 1 SCR 688", "ipeelee": "*R v Ipeelee*, 2012 SCC 13"}


def render(source, pinpoint):
    return CASES[source] + (f" {pinpoint}" if pinpoint else "")


def note(identity, source="gladue", pinpoint=""):
    return {"id": identity, "citations": [{"source": source, "pinpoint": pinpoint}]}


def kinds(result):
    return [n["citations"][0]["kind"] if n["citations"] else None for n in result]


# ── Resolver (cases carried over from the earlier preview) ─────────────

def test_full_ibid_supra_and_ibid_after_supra():
    notes = [note("a", pinpoint="at para 20"), note("b", pinpoint="at para 20"),
             note("c", "ipeelee"), note("d", pinpoint="at para 30"), note("e", pinpoint="at para 31")]
    result = footnotes.resolve(notes, {"gladue": "Gladue"}, render)
    assert kinds(result) == ["full", "ibid", "full", "supra", "ibid"]
    assert result[0]["text"] == "*R v Gladue*, [1999] 1 SCR 688 at para 20 [*Gladue*]."
    assert result[1]["text"] == "*Ibid*."
    assert result[3]["text"] == "*Gladue*, *supra* note 1 at para 30."
    assert result[4]["text"] == "*Ibid* at para 31."
    assert result[3]["citations"][0]["target_note"] == 1 and result[4]["citations"][0]["target_note"] == 4


def test_reorder_insert_and_delete_first_full_recompute_every_reference():
    notes = [note("a"), note("b", "ipeelee"), note("c", pinpoint="at para 30")]
    forms = {"gladue": "Gladue"}
    assert footnotes.resolve(notes, forms, render)[2]["text"] == "*Gladue*, *supra* note 1 at para 30."
    moved = footnotes.resolve([notes[1], notes[0], notes[2]], forms, render)
    assert kinds(moved)[1:] == ["full", "ibid"]
    deleted = footnotes.resolve(notes[1:], forms, render)
    assert deleted[1]["citations"][0]["kind"] == "full" and "supra" not in deleted[1]["text"]
    inserted = footnotes.resolve([{"id": "text", "citations": []}, *notes], forms, render)
    assert inserted[3]["citations"][0]["target_note"] == 2


def test_multiple_sources_and_empty_notes_break_the_ibid_chain():
    multi = {"id": "multi", "citations": note("x")["citations"] + note("y", "ipeelee")["citations"]}
    result = footnotes.resolve([multi, note("b"), {"id": "empty", "citations": []}, note("c")], {"gladue": "Gladue"}, render)
    assert "; " in result[0]["text"]
    assert result[1]["citations"][0]["kind"] == "supra" and result[3]["citations"][0]["kind"] == "supra"


def test_without_short_form_repeat_full_and_do_not_invent_labels():
    result = footnotes.resolve([note("a"), note("b", "ipeelee"), note("c")], {}, render)
    assert result[2]["citations"][0]["kind"] == "full" and "supra" not in result[2]["text"]
    single = footnotes.resolve([note("a")], {"gladue": "Gladue"}, render)[0]
    assert "[" not in single["text"].split("SCR")[1]


def test_whole_source_after_specific_pinpoint_does_not_inherit_locator():
    result = footnotes.resolve([note("a", pinpoint="at para 20"), note("b")], {}, render)
    assert result[1]["citations"][0]["kind"] == "full" and "para 20" not in result[1]["text"]


def test_all_small_permutations_have_valid_backward_targets_and_are_deterministic():
    original = [note("a", pinpoint="at para 20"), note("b", "ipeelee"), note("c", pinpoint="at para 30"), note("d", "ipeelee")]
    snapshot = deepcopy(original)
    forms = {"gladue": "Gladue", "ipeelee": "Ipeelee"}
    for order in permutations(original):
        result = footnotes.resolve(list(order), forms, render)
        assert result == footnotes.resolve(list(order), forms, render)
        for current in result:
            ref = current["citations"][0]
            if ref["target_note"] is not None:
                target = result[ref["target_note"] - 1]
                assert target["number"] < current["number"]
                assert target["citations"][0]["source"] == ref["source"]
                if ref["kind"] == "supra":
                    assert target["citations"][0]["kind"] == "full"
                else:
                    assert target["number"] == current["number"] - 1
    assert original == snapshot


def test_colliding_short_forms_and_reversed_ranges_are_refused():
    with pytest.raises(ValueError, match="distinguish"):
        footnotes.resolve([note("a")], {"gladue": "Same", "ipeelee": "same"}, render)
    with pytest.raises(ValueError, match="lower number"):
        footnotes.clean_pinpoint("at paras 99-1")
    assert footnotes.clean_pinpoint("para 64") == "at para 64"


# ── Plugin: documents, revisions, request ids ──────────────────────────

@pytest.fixture
def ctx(tmp_path):
    h = Harness(discover(), model="m", config_path=tmp_path / "h.json")
    session, token = h.sessions.start()
    session.messages.append({"role": "user", "content": "Cite Gladue at para 64 please."})
    context = h.context(session, "footnotes")
    context.token = token
    context.cases = {
        "gladue": session.records.put(Record(plugin.CASE_TYPE, {
            "style_of_cause": Field("R v Gladue", "database"), "reporter": Field("[1999] 1 SCR 688", "database")},
            "a2aj", "gladue")),
        "ipeelee": session.records.put(Record(plugin.CASE_TYPE, {
            "style_of_cause": Field("R v Ipeelee", "user"), "neutral_citation": Field("2012 SCC 13", "user")},
            "user", "ipeelee")),
    }
    return context


def create(ctx, request_id="new"):
    return plugin.create(ctx, plugin.CreateParams(title="Sentencing paper", request_id=request_id)).content["document"]


def edit(ctx, document, request_id, op, **values):
    return plugin.edit(ctx, plugin.EditParams(document=document, request_id=request_id, op=op, **values)).content


def cite(ctx, name, pinpoint=""):
    return {"ref": ctx.cases[name], "pinpoint": pinpoint}


def test_document_edits_renumber_and_keep_references_valid(ctx):
    doc = create(ctx)
    doc = edit(ctx, doc, "1", "insert", citations=[cite(ctx, "gladue", "at para 64")])["document"]
    doc = edit(ctx, doc, "2", "insert", citations=[cite(ctx, "ipeelee")])["document"]
    doc = edit(ctx, doc, "3", "short_form", source=ctx.cases["gladue"], short_form="Gladue")["document"]
    report = edit(ctx, doc, "4", "insert", citations=[cite(ctx, "gladue", "at para 81")])
    assert report["text"].splitlines() == [
        "1 *R v Gladue*, [1999] 1 SCR 688 at para 64 [*Gladue*].",
        "2 *R v Ipeelee*, 2012 SCC 13.",
        "3 *Gladue*, *supra* note 1 at para 81."]
    # A new first note shifts every number; the supra follows its full citation.
    report = edit(ctx, report["document"], "5", "insert", position=1, citations=[cite(ctx, "ipeelee")])
    assert report["text"].splitlines()[3] == "4 *Gladue*, *supra* note 2 at para 81."
    first_gladue = report["notes"][1]["id"]
    report = edit(ctx, report["document"], "6", "delete", note_id=first_gladue)
    assert report["text"].splitlines()[2] == "3 *R v Gladue*, [1999] 1 SCR 688 at para 81."
    assert report["rules_reviewed"] is False and report["rule_version"] == footnotes.RULE_VERSION


def test_stale_revision_is_refused_and_retries_are_idempotent(ctx):
    doc = create(ctx)
    first = edit(ctx, doc, "r1", "insert", citations=[cite(ctx, "gladue")])
    again = edit(ctx, doc, "r1", "insert", citations=[cite(ctx, "gladue")])
    assert again["replayed"] and again["document"] == first["document"] and len(again["notes"]) == 1
    with pytest.raises(ValueError, match="different change"):
        edit(ctx, doc, "r1", "insert", citations=[cite(ctx, "ipeelee")])
    with pytest.raises(ValueError, match="Revision conflict"):
        edit(ctx, doc, "r2", "insert", citations=[cite(ctx, "ipeelee")])
    assert create(ctx, "new") == doc       # creating is idempotent too


def test_saved_document_identity_occurrences_and_retries_survive_restart(ctx):
    doc = create(ctx)
    first = edit(ctx, doc, "insert", "insert", citations=[cite(ctx, "gladue")])
    occurrence = first["notes"][0]["citations"][0]["id"]
    changed = edit(ctx, first["document"], "update", "update", note_id=first["notes"][0]["id"],
                   citations=[cite(ctx, "gladue", "at para 64")])
    assert changed["document_id"] == first["document_id"]
    assert changed["notes"][0]["citations"][0]["id"] == occurrence
    assert ctx.session.retained
    ctx._harness.sessions.save(ctx.session)
    again = Harness(discover(), model="m", config_path=ctx._harness.config_path)
    revived = again.sessions.get(ctx.session.id, ctx.token)
    rc = again.context(revived, "footnotes")
    report = plugin.render_document(rc, changed["document"])
    assert report["document_id"] == first["document_id"] and report["notes"] == changed["notes"]
    replay = plugin.edit(rc, plugin.EditParams(document=doc, request_id="insert", op="insert",
                                             citations=[cite(ctx, "gladue")])).content
    assert replay["replayed"] and replay["document"] == first["document"]
    with pytest.raises(ValueError, match="Revision conflict"):
        plugin.edit(rc, plugin.EditParams(document=doc, request_id="another", op="delete", note_id=report["notes"][0]["id"]))


def test_verification_and_pinpoint_origin_stay_visible(ctx):
    doc = create(ctx)
    doc = edit(ctx, doc, "1", "insert", citations=[cite(ctx, "gladue", "at para 64")])["document"]
    report = edit(ctx, doc, "2", "insert", citations=[cite(ctx, "ipeelee", "at para 20")])
    gladue, ipeelee = report["notes"][0]["citations"][0], report["notes"][1]["citations"][0]
    assert gladue["source_verified"] is True and gladue["pinpoint_origin"] == "user"
    assert ipeelee["source_verified"] is False and ipeelee["pinpoint_origin"] == "model"


def test_neutral_citation_needs_paragraph_pinpoints_and_cases_only(ctx):
    doc = create(ctx)
    with pytest.raises(ValueError, match="incompatible"):
        edit(ctx, doc, "1", "insert", citations=[cite(ctx, "ipeelee", "at 20")])
    statute = ctx.records.put(Record("legislation", {"title": Field("Criminal Code", "database")}, "x", "cc"))
    with pytest.raises(ValueError, match="case citations only"):
        edit(ctx, doc, "2", "insert", citations=[{"ref": statute}])
    with pytest.raises(ValueError, match="once per note"):
        edit(ctx, doc, "3", "insert", citations=[cite(ctx, "gladue"), cite(ctx, "gladue", "at para 2")])


def test_newer_record_version_is_reported_and_only_pinned_explicitly(ctx):
    doc = create(ctx)
    doc = edit(ctx, doc, "1", "insert", citations=[cite(ctx, "gladue")])["document"]
    newer = ctx.records.put(Record(plugin.CASE_TYPE, {
        "style_of_cause": Field("R v Gladue", "database"), "reporter": Field("[1999] 1 SCR 689", "database")},
        "a2aj", "gladue"), supersedes=ctx.cases["gladue"])
    report = plugin.render(ctx, plugin.RenderParams(document=doc)).content
    assert "688" in report["text"]
    assert any(i["code"] == "newer_source_version" and i["current_ref"] == newer for i in report["issues"])
    report = edit(ctx, doc, "2", "pin_source", source=ctx.cases["gladue"])
    assert "689" in report["text"] and not report["issues"]


def test_random_edit_sequences_keep_every_reference_pointing_backwards(ctx):
    rng = random.Random(7)
    doc = create(ctx)
    doc = edit(ctx, doc, "sf0", "insert", citations=[cite(ctx, "gladue")])["document"]
    doc = edit(ctx, doc, "sf1", "short_form", source=ctx.cases["gladue"], short_form="Gladue")["document"]
    for step in range(40):
        report = plugin.render(ctx, plugin.RenderParams(document=doc)).content
        ids = [n["id"] for n in report["notes"]]
        op = rng.choice(["insert", "insert", "move", "delete"] if ids else ["insert"])
        values = {}
        if op == "insert":
            values = {"position": rng.randint(1, len(ids) + 1),
                      "citations": [cite(ctx, rng.choice(["gladue", "ipeelee"]), rng.choice(["", "at para 3", "at para 9"]))]}
        elif op == "move":
            values = {"note_id": rng.choice(ids), "position": rng.randint(1, len(ids))}
        else:
            values = {"note_id": rng.choice(ids)}
        report = edit(ctx, doc, f"step{step}", op, **values)
        doc = report["document"]
        for current in report["notes"]:
            for ref in current["citations"]:
                if ref["target_note"] is None:
                    continue
                target = report["notes"][ref["target_note"] - 1]
                assert target["number"] < current["number"]
                assert any(c["source"] == ref["source"] and (ref["kind"] == "ibid" or c["kind"] == "full")
                           for c in target["citations"])
