"""The function plugins: mcgill citations, quote checking, bibliography.

Flows that involve user-quoted parameters or provenance auditing run through
the harness itself (``_execute``), because that is where the checks live.
"""
import json
from unittest.mock import patch

import pytest

from core.tool_contracts import Artifact, Derivation, Field, Finding, Record, is_grounded
from harness.core import Harness
from harness.plugin import discover
from harness.session import Context

TEXT = ("R. v. Sharma\n[1] Conditional sentences are a form of punishment.\n"
        "[2] Parliament created the conditional sentencing regime in 1996.\n")
URL = "https://decisions.scc-csc.ca/x"
QUOTE = "Parliament created the conditional sentencing regime in 1996."


def make():
    h = Harness(discover(), model="m")
    session, _ = h.sessions.start()
    return h, session


def ctx_for(h, session, name):
    return Context(session, name, h)


def gladue_record():
    return Record("jurisprudence", {
        "style_of_cause": Field("R v Gladue", "database", source_id="a2aj:c1"),
        "neutral_citation": Field("[1999] 1 SCR 688", "database", source_id="a2aj:c1"),
    }, "a2aj", "c1")


def run_tool(h, session, tool, **arguments):
    """A tool call through the harness: params verified, audit on save."""
    plugin_name = tool.partition("__")[0]
    h.set_enabled(plugin_name, True)
    if plugin_name not in session.loaded:
        session.loaded.append(plugin_name)
    call = {"id": "c1", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)}}
    return h._execute(session, call)[1]


def test_mcgill_cite_renders_and_verifies_from_the_chain():
    h, session = make()
    from plugins import mcgill
    ref = session.records.put(gladue_record())
    result = mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=ref))
    artifact = session.records.get(result.content["ref"], Artifact)
    assert artifact.kind == "citation_text"
    assert artifact.content == "*R v Gladue*, [1999] 1 SCR 688."
    assert result.content["verified"] is True
    meta = session.records.meta(result.content["ref"])
    assert meta["fields"]["neutral_citation"]["origin"] == "database"


def test_mcgill_cite_lists_what_is_missing():
    h, session = make()
    from plugins import mcgill
    ref = session.records.put(Record("book", {"author": Field("H. L. A. Hart", "database", source_id="ol:1"),
                                              "title": Field("The Concept of Law", "database", source_id="ol:1")},
                                     "openlibrary", "OL1"))
    with pytest.raises(ValueError) as exc:
        mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=ref))
    assert "Place of publication" in str(exc.value)


def test_user_pinpoint_makes_the_citation_honest_about_being_unverified():
    h, session = make()
    ref = session.records.put(gladue_record())
    session.messages.append({"role": "user", "content": "帮我引用，定位到 at para 64"})
    result = run_tool(h, session, "mcgill__cite", ref=ref, pinpoint="at para 64")
    assert result.content["verified"] is False
    assert "at para 64" in result.content["citation"]
    assert result.content["citation"].count("at para 64") == 1   # the template's own "at" is not doubled


def test_a_pinpoint_the_model_supplied_is_labelled_not_refused():
    """A pinpoint the user never wrote has no source in this session: the
    citation is still produced, and it reports itself unverified."""
    h, session = make()
    ref = session.records.put(gladue_record())
    session.messages.append({"role": "user", "content": "引用它"})
    result = run_tool(h, session, "mcgill__cite", ref=ref, pinpoint="at para 999")
    assert "error" not in result.content
    assert "para 999" in result.content["citation"]
    assert result.content["verified"] is False


def test_full_text_then_quote_then_cite_stays_verified():
    h, session = make()
    ref = session.records.put(gladue_record())
    session.messages.append({"role": "user", "content": f"核对：{QUOTE}"})
    with patch("core.quote_check.judgment",
               return_value={"text": TEXT, "url": URL, "citations": ["[1999] 1 SCR 688"]}):
        version = run_tool(h, session, "a2aj__full_text", ref=ref)
        checked = run_tool(h, session, "quote__check", ref=version.content["ref"], quote=QUOTE)
    assert checked.content["verdict"] == "exact"
    cited = run_tool(h, session, "mcgill__cite", ref=ref, pinpoint_from=checked.content["pinpoint_ref"])
    assert cited.content["verified"] is True
    assert "at para 2" in cited.content["citation"]
    assert cited.content["citation"].count("at para 2") == 1     # no doubled "at"


def test_quote_needs_the_stored_full_text():
    h, session = make()
    from plugins import quote
    ref = session.records.put(gladue_record())          # no full_text field
    session.messages.append({"role": "user", "content": f"核对：{QUOTE}"})
    with pytest.raises(ValueError) as exc:
        quote.check(ctx_for(h, session, "quote"), quote.CheckParams(ref=ref, quote=QUOTE))
    assert "full text" in str(exc.value)


def test_quote_pinpoint_artifact_is_database_grounded():
    h, session = make()
    ref = session.records.put(Record("jurisprudence", {
        "style_of_cause": Field("R v Sharma", "database", source_id=URL),
        "neutral_citation": Field("2022 SCC 39", "database", source_id=URL),
    }, "a2aj", "c9"))
    session.messages.append({"role": "user", "content": f"核对这句：{QUOTE}"})
    with patch("core.quote_check.judgment",
               return_value={"text": TEXT, "url": URL, "citations": ["2022 SCC 39"]}):
        version = run_tool(h, session, "a2aj__full_text", ref=ref)
        result = run_tool(h, session, "quote__check", ref=version.content["ref"], quote=QUOTE)
    pinpoint = session.records.get(result.content["pinpoint_ref"], Artifact)
    assert pinpoint.kind == "pinpoint" and is_grounded(pinpoint.derivation)
    finding = session.records.get(result.content["finding"], Finding)
    assert finding.verdict == "confirmed"
    # The Finding's source leaf names exactly the stored full text.
    source = finding.derivation.inputs[1]
    version_record = session.records.get(version.content["ref"], Record)
    assert source.value == version_record.fields["full_text"].value
    assert source.source_id == version_record.fields["full_text"].source_id


def test_bibliography_omits_pinpoints_and_keeps_verification():
    h, session = make()
    from plugins import bibliography, mcgill
    book = Record("book", {"author": Field("Patrick Olivelle", "database", source_id="ol:1"),
                           "title": Field("The Early Upanisads", "database", source_id="ol:1"),
                           "place": Field("New York", "database", source_id="ol:1"),
                           "publisher": Field("Oxford University Press", "database", source_id="ol:1"),
                           "year": Field("1998", "database", source_id="ol:1"),
                           "pinpoint": Field("at 12", "user")},
                  "openlibrary", "OL1")
    book_ref = session.records.put(book)
    citation = mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=book_ref))
    assert citation.content["verified"] is False      # the user's pinpoint
    result = bibliography.build(ctx_for(h, session, "bibliography"),
                                bibliography.BuildParams(refs=[citation.content["ref"]]))
    assert result.content["unverified"] == 0          # the bibliography drops the pinpoint
    artifact = session.records.get(result.content["ref"], Artifact)
    assert artifact.kind == "bibliography"
    assert "The Early Upanisads" in result.blocks[0]["body"]


def test_deadline_artifact_is_stored_with_its_inputs():
    h, session = make()
    result = run_tool(h, session, "deadlines__compute", start="2026-09-25", days=10,
                      mode="court_days", rule="r 3.02", holidays=["2026-10-12"])
    artifact = session.records.get(result.content["ref"], Artifact)
    assert artifact.kind == "deadline_date"
    leaves = artifact.derivation.inputs
    assert all(isinstance(node, Field) and node.origin == "user" for node in leaves[:3])


def test_a2aj_full_text_attaches_a_database_field():
    h, session = make()
    ref = session.records.put(gladue_record())
    with patch("core.quote_check.judgment",
               return_value={"text": "full judgment text", "url": URL, "citations": ["[1999] 1 SCR 688"]}):
        result = run_tool(h, session, "a2aj__full_text", ref=ref)
    version = session.records.get(result.content["ref"], Record)
    assert version.fields["full_text"].origin == "database"
    assert version.fields["full_text"].source_id == URL
    assert version.fields["style_of_cause"].value == "R v Gladue"


def test_quote_needs_a_database_citation():
    h, session = make()
    from plugins import quote
    ref = session.records.put(Record("jurisprudence", {
        "style_of_cause": Field("R v X", "user")}, "user", "blank"))
    session.messages.append({"role": "user", "content": "some quote words here please"})
    with pytest.raises(ValueError):
        quote.check(ctx_for(h, session, "quote"),
                    quote.CheckParams(ref=ref, quote="some quote words here please"))


# ── The provenance audit: fabricated leaves cannot become "verified" ──


def fabricated(kind: str = "database"):
    return Artifact("citation_text", "some citation",
                    Derivation((Field("R v Fabricated", kind, source_id="nowhere:1"),), "cite.render.v1", ("x",)))


def test_a_fabricated_database_leaf_is_refused():
    h, session = make()
    ctx = ctx_for(h, session, "mcgill")
    with pytest.raises(Exception) as exc:
        ctx.save(fabricated("database"))
    assert "没有出处" in str(exc.value)
    assert session.records.all(Artifact) == []


def test_a_stored_field_passes_the_audit_but_only_as_stored():
    h, session = make()
    session.records.put(gladue_record())
    ctx = ctx_for(h, session, "mcgill")
    real = session.records.get("rec_1", Record).fields["neutral_citation"]
    ref = ctx.save(Artifact("citation_text", "*R v Gladue*, [1999] 1 SCR 688.",
                            Derivation((real,), "cite.render.v1", ("neutral_citation",))))
    assert ref == "art_2"          # numbering is global: rec_1, then art_2
    # A fresh Field carrying the same value and source id traces the same way.
    twin = Field(real.value, "database", source_id=real.source_id)
    ctx.save(Artifact("citation_text", "twin", Derivation((twin,), "cite.render.v1", ("x",))))


def test_a_computed_leaf_without_a_derivation_is_refused():
    h, session = make()
    ctx = ctx_for(h, session, "mcgill")
    bad = Artifact("x", "y", Derivation((Field("out", "computed", rule_id="r:v1"),), "outer:v1"))
    with pytest.raises(Exception) as exc:
        ctx.save(bad)
    assert "推导链" in str(exc.value)


def test_a_user_leaf_from_thin_air_is_refused():
    h, session = make()
    session.records.put(gladue_record())
    ctx = ctx_for(h, session, "mcgill")
    bad = Artifact("x", "y", Derivation((Field("the user said this", "user"),), "cite.render.v1"))
    with pytest.raises(Exception) as exc:
        ctx.save(bad)
    assert "user 叶子" in str(exc.value)


def test_an_extracted_leaf_needs_session_evidence():
    h, session = make()
    ctx = ctx_for(h, session, "mcgill")
    bad = Artifact("x", "y", Derivation((Field("scanned text", "extracted"),), "cite.render.v1"))
    with pytest.raises(Exception) as exc:
        ctx.save(bad)
    assert "extracted" in str(exc.value)
    # Once a file extraction stored it, the same value traces.
    session.records.put(Record("document", {"text": Field("scanned text", "extracted")}, "file", "att_1"))
    ctx.save(Artifact("x", "y", Derivation((Field("scanned text", "extracted"),), "cite.render.v1")))


def test_every_record_type_a_source_produces_has_a_mcgill_rule():
    """A data source's record is citable from its own fields -- never by
    making the user retype them as unverified user fields."""
    from core.mcgill_format import schemas
    produced = {"jurisprudence", "legislation", "bill", "journal_article", "book"}   # core/source_tools.py
    assert produced <= set(schemas())


def test_a_legisinfo_bill_renders_by_the_bills_rule():
    from core.mcgill_format import mcgill_clean, render_fields
    values = {"number": "C-63", "title": "An Act to enact the Online Harms Act",
              "session": "1", "parliament": "44", "year": "2024"}
    text = render_fields("bill", {k: mcgill_clean(k, v) for k, v in values.items()})
    assert text == "Bill C-63, *An Act to enact the Online Harms Act*, 1st Sess, 44th Parl, 2024."


def test_a_leaf_read_out_of_a_stored_page_passes_the_audit():
    """A fact contained in a stored page's text traces to that page -- the
    Donoghue year read out of a fetched case report is sourced, not invented.
    A value the page never says still does not trace."""
    h, session = make()
    session.records.put(Record("website", {
        "text": Field("Case report 1932 HL. Donoghue v. Stevenson. 26 May 1932. Lord Atkin.",
                      "extracted", source_id="https://case.report"),
    }, "web", "https://case.report"))
    ctx = ctx_for(h, session, "mcgill")
    ref = ctx.save(Artifact("citation_text", "26 May 1932",
                            Derivation((Field("26 May 1932", "extracted", source_id="https://case.report"),),
                                       "cite.render.v1", ("date",))))
    assert ref == "art_2"
    with pytest.raises(Exception) as exc:
        ctx.save(Artifact("x", "y", Derivation(
            (Field("31 December 2099", "extracted", source_id="https://case.report"),), "cite.render.v1")))
    assert "没有出处" in str(exc.value)


def test_a_database_leaf_quoted_from_stored_full_text_passes():
    """Same rule for database full text: a quotation located in the stored
    judgment traces to the same source id."""
    h, session = make()
    session.records.put(Record("jurisprudence", {
        "full_text": Field(TEXT, "database", source_id=URL),
    }, "a2aj", "c1"))
    ctx = ctx_for(h, session, "mcgill")
    ctx.save(Artifact("x", "y", Derivation(
        (Field("conditional sentencing regime in 1996", "database", source_id=URL),), "cite.render.v1")))


def test_a_database_leaf_from_a_different_source_id_still_fails():
    h, session = make()
    session.records.put(Record("jurisprudence", {
        "full_text": Field(TEXT, "database", source_id=URL),
    }, "a2aj", "c1"))
    ctx = ctx_for(h, session, "mcgill")
    with pytest.raises(Exception) as exc:
        ctx.save(Artifact("x", "y", Derivation(
            (Field("conditional sentencing regime in 1996", "database", source_id="a2aj:other"),), "cite.render.v1")))
    assert "没有出处" in str(exc.value)
