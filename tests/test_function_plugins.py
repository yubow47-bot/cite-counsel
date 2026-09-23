"""The function plugins: mcgill citations, quote checking, bibliography."""
from unittest.mock import patch

import pytest

from core.tool_contracts import Artifact, Field, Finding, Record
from harness.core import Harness
from harness.plugin import discover
from harness.session import Context

TEXT = ("R. v. Sharma\n[1] Conditional sentences are a form of punishment.\n"
        "[2] Parliament created the conditional sentencing regime in 1996.\n")
URL = "https://decisions.scc-csc.ca/x"


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


def store_record(session, record):
    return session.records.put(record)


def test_mcgill_cite_renders_and_verifies_from_the_chain():
    h, session = make()
    from plugins import mcgill
    ref = store_record = session.records.put(gladue_record())
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
    from core import mcgill_format
    ref = session.records.put(Record("book", {"author": Field("H. L. A. Hart", "database", source_id="ol:1"),
                                              "title": Field("The Concept of Law", "database", source_id="ol:1")},
                                     "openlibrary", "OL1"))
    with pytest.raises(ValueError) as exc:
        mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=ref))
    assert "出版地" in str(exc.value)


def test_user_pinpoint_makes_the_citation_honest_about_being_unverified():
    h, session = make()
    from plugins import mcgill
    session.messages.append({"role": "user", "content": "帮我引用，定位到 at para 64"})
    ref = session.records.put(gladue_record())
    result = mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=ref, pinpoint="at para 64"))
    assert result.content["verified"] is False
    assert "at para 64" in result.content["citation"]


def test_pinpoint_from_the_quote_check_stays_verified():
    h, session = make()
    from plugins import mcgill, quote
    ref = session.records.put(gladue_record())
    with patch("core.quote_check.judgment", return_value={"text": quote_text(), "url": URL,
                                                          "citations": ["[1999] 1 SCR 688"]}):
        session.messages.append({"role": "user", "content": "核对：Parliament created the conditional sentencing regime in 1996."})
        checked = _plugin_quote(h, session).check(ctx_for(h, session, "quote"), _plugin_quote_params(
            ref=ref, quote="Parliament created the conditional sentencing regime in 1996."))
    assert checked.content["verdict"] == "exact"
    pinpoint_ref = checked.content["pinpoint_ref"]
    assert pinpoint_ref
    cited = mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=ref, pinpoint_from=pinpoint_ref))
    assert cited.content["verified"] is True
    assert "at para 2" in cited.content["citation"]


def _plugin_quote(h, session):
    from plugins import quote
    return quote


def _plugin_quote_params(**kwargs):
    from plugins import quote
    return quote.CheckParams(**kwargs)


def test_quote_check_rejects_a_model_invented_quote():
    h, session = make()
    from plugins import quote
    ref = session.records.put(gladue_record())
    with pytest.raises(ValueError):
        quote.check(ctx_for(h, session, "quote"),
                    quote.CheckParams(ref=ref, quote="The Charter guarantees a right to a jury trial."))


def test_quote_pinpoint_artifact_is_database_grounded():
    h, session = make()
    from plugins import quote
    ref = session.records.put(Record("jurisprudence", {
        "style_of_cause": Field("R v Sharma", "database", source_id=URL),
        "neutral_citation": Field("2022 SCC 39", "database", source_id=URL),
    }, "a2aj", "c9"))
    session.messages.append({"role": "user", "content": "核对这句：Parliament created the conditional sentencing regime in 1996."})
    with patch("core.quote_check.judgment", return_value={"text": TEXT, "url": URL, "citations": ["2022 SCC 39"]}):
        result = quote.check(ctx_for(h, session, "quote"), quote.CheckParams(
            ref=ref, quote="Parliament created the conditional sentencing regime in 1996."))
    pinpoint = session.records.get(result.content["pinpoint_ref"], Artifact)
    from core.tool_contracts import is_grounded
    assert pinpoint.kind == "pinpoint" and is_grounded(pinpoint.derivation)
    finding = session.records.get(result.content["finding"], Finding)
    assert finding.verdict == "confirmed"


def quote_text():
    return TEXT


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
    assert "at 12" not in result.content["note"]
    artifact = session.records.get(result.content["ref"], Artifact)
    assert artifact.kind == "bibliography"
    assert "*The Early Upanisads*" in result.content["note"] or "The Early Upanisads" in result.blocks[0]["body"]


def test_deadline_artifact_is_stored_with_its_inputs():
    h, session = make()
    from plugins import deadlines
    result = deadlines_compute(h, session)
    artifact = session.records.get(result.content["ref"], Artifact)
    assert artifact.kind == "deadline_date"
    leaves = artifact.derivation.inputs
    assert all(isinstance(node, Field) and node.origin == "user" for node in leaves[:3])


def deadlines_compute(h, session):
    from plugins import deadlines
    return deadlines.compute(ctx_for(h, session, "deadlines"), deadlines.DeadlineParams(
        start="2026-09-25", days=10, mode="court_days", rule="r 3.02", holidays=["2026-10-12"]))


def test_a2aj_full_text_attaches_a_database_field():
    h, session = make()
    from plugins import a2aj
    ref = session.records.put(gladue_record())
    with patch("core.quote_check.judgment",
               return_value={"text": "full judgment text", "url": URL, "citations": ["[1999] 1 SCR 688"]}):
        result = a2aj_full_text(h, session, ref)
    version = session.records.get(result.content["ref"], Record)
    assert version.fields["full_text"].origin == "database"
    assert version.fields["full_text"].source_id == URL
    assert version.fields["style_of_cause"].value == "R v Gladue"


def a2aj_full_text(h, session, ref):
    from plugins import a2aj
    return a2aj.full_text(ctx_for(h, session, "a2aj"), a2aj.FullTextParams(ref=ref))


def test_quote_needs_a_database_citation():
    h, session = make()
    from plugins import quote
    ref = session.records.put(Record("jurisprudence", {
        "style_of_cause": Field("R v X", "user")}, "user", "blank"))
    session.messages.append({"role": "user", "content": "some quote words here please"})
    with pytest.raises(ValueError):
        quote.check(ctx_for(h, session, "quote"),
                    quote.CheckParams(ref=ref, quote="some quote words here please"))