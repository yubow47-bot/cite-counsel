"""The function plugins: mcgill citations, quote checking, bibliography.

Flows that involve user-quoted parameters or provenance auditing run through
the harness itself (``_execute``), because that is where the checks live.
"""
import json
from unittest.mock import patch

import pytest

from core.tool_contracts import Artifact, Derivation, Field, Finding, Record, derivation_leaves, is_grounded
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
    assert all(isinstance(node, Field) and node.origin == "model" for node in leaves[:3])
    assert result.content["inputs"][0]["origin"] == "model"
    assert any(item["name"] == "weekend_days" and item["origin"] == "default"
               for item in result.content["inputs"])


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


def leaf_origins(session, ref):
    return [leaf.origin for leaf in derivation_leaves(session.records.get(ref).derivation)]


def test_a_fabricated_database_leaf_is_stored_as_unverified():
    """Not refused -- the user still gets the artifact -- but the forged leaf
    loses its "database" standing, so the artifact cannot count as verified."""
    h, session = make()
    ctx = ctx_for(h, session, "mcgill")
    ref = ctx.save(fabricated("database"))
    assert leaf_origins(session, ref) == ["model"]
    assert not is_grounded(session.records.get(ref).derivation)


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


def test_a_user_leaf_from_thin_air_is_stored_as_unverified():
    h, session = make()
    session.records.put(gladue_record())
    ctx = ctx_for(h, session, "mcgill")
    ref = ctx.save(Artifact("x", "y", Derivation((Field("the user said this", "user"),), "cite.render.v1")))
    assert leaf_origins(session, ref) == ["model"]


def test_an_extracted_leaf_needs_session_evidence():
    h, session = make()
    ctx = ctx_for(h, session, "mcgill")
    bad = Artifact("x", "y", Derivation((Field("scanned text", "extracted"),), "cite.render.v1"))
    assert leaf_origins(session, ctx.save(bad)) == ["model"]
    # Once a file extraction stored it, the same value traces.
    session.records.put(Record("document", {"text": Field("scanned text", "extracted")}, "file", "att_1"))
    ref = ctx.save(Artifact("x", "y", Derivation((Field("scanned text", "extracted"),), "cite.render.v1")))
    assert leaf_origins(session, ref) == ["extracted"]


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
    ref = ctx.save(Artifact("x", "y", Derivation(
        (Field("31 December 2099", "extracted", source_id="https://case.report"),), "cite.render.v1")))
    assert leaf_origins(session, ref) == ["model"]


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


def test_a_database_leaf_from_a_different_source_id_is_downgraded():
    h, session = make()
    session.records.put(Record("jurisprudence", {
        "full_text": Field(TEXT, "database", source_id=URL),
    }, "a2aj", "c1"))
    ctx = ctx_for(h, session, "mcgill")
    ref = ctx.save(Artifact("x", "y", Derivation(
        (Field("conditional sentencing regime in 1996", "database", source_id="a2aj:other"),), "cite.render.v1")))
    assert leaf_origins(session, ref) == ["model"]


def _clean(values):
    from core.mcgill_format import mcgill_clean
    return {name: mcgill_clean(name, value) for name, value in values.items()}


def test_constitutional_form_arranges_the_guides_own_examples_from_fields():
    """Only the form lives in the plugin: given the parts as a source prints
    them, it reproduces the Guide's Constitutional Statutes examples."""
    import json as _json
    from core.mcgill_format import _RULES_PATH, render_fields
    topic = next(t for t in _json.loads(_RULES_PATH.read_text(encoding="utf-8"))["legislation"]["topics"]
                 if t["topic"] == "Constitutional Statutes")
    rendered = [
        render_fields("constitutional", _clean({
            "title": "Constitution Act, 1982", "pinpoint": "s 35",
            "enacted_as": "Schedule B to the Canada Act 1982, 1982, c. 11 (U.K.)"})),
        render_fields("constitutional", _clean({
            "title": "Canadian Charter of Rights and Freedoms", "pinpoint": "s 7",
            # As Justice Laws prints them: a heading in capitals, the enactment in its own style.
            "part": "PART I", "part_of": "CONSTITUTION ACT, 1982",
            "enacted_as": "Schedule B to the Canada Act 1982 , 1982, c. 11 (U.K.)"})),
        render_fields("constitutional", _clean({
            "title": "Constitution Act, 1867", "jurisdiction": "UK", "citation": "30 & 31 Vict, c 3",
            "pinpoint": "s 91", "reprinted_in": "RSC 1985, Appendix II, No 5"})),
    ]
    assert rendered == topic["examples"]


def test_the_constitutional_form_adds_nothing_the_record_lacks():
    """No part of the enactment comes from the plugin: without what the Act
    was enacted as there is no citation, and a Part needs its Act."""
    from core.mcgill_format import missing_summary, render_fields
    with pytest.raises(ValueError) as exc:
        render_fields("constitutional", _clean({"title": "Canadian Charter of Rights and Freedoms",
                                                "part": "PART I", "part_of": "CONSTITUTION ACT, 1982"}))
    assert "What the Act was enacted as" in str(exc.value)
    gap = missing_summary("constitutional", {"title": "Canadian Charter of Rights and Freedoms", "part": "PART I",
                                             "enacted_as": "Schedule B to the Canada Act 1982, 1982, c. 11 (U.K.)"})
    assert gap.startswith("The Act that Part belongs to")


def test_a_part_of_value_that_repeats_the_part_is_sent_back():
    from core.mcgill_format import field_problem
    assert "the Act's title alone" in field_problem("constitutional", "part_of",
                                                    "Part I of the Constitution Act, 1982")
    assert field_problem("constitutional", "part", "PART I") is None


CHARTER_PAGE = "https://laws-lois.justice.gc.ca/eng/Const/page-12.html"
CONST_INDEX = "https://laws.justice.gc.ca/eng/Const/Const_index.html"


def _page(session, url, text):
    return session.records.put(Record("website", {
        "title": Field("The Constitution Acts 1867 to 1982", "extracted", source_id=url),
        "url": Field(url, "extracted", source_id=url),
        "text": Field(text, "extracted", source_id=url),
    }, "web", url))


def _justice_laws_pages(session):
    """Readable source text with explicit token boundaries; quotes preserve its spacing."""
    charter = _page(session, CHARTER_PAGE, "THE CONSTITUTION ACTS 1867 to 1982 [771 KB] CONSTITUTION ACT, 1982 End "
                                           "note (81) PART I Canadian Charter of Rights and Freedoms Whereas Canada "
                                           "is founded upon principles")
    index = _page(session, CONST_INDEX, "The Constitution Act, 1982 was enacted as Schedule B to the Canada Act "
                                        "1982 , 1982, c. 11 (U.K.). It is set out in this consolidation")
    return charter, index


def _compose_charter(h, session, charter, index):
    import harness.core as harness_core
    return h._run_builtin(session, harness_core.BUILTIN_TOOLS[0], {
        "record_type": "constitutional",
        "fields": [
            {"name": "title", "value": "Canadian Charter of Rights and Freedoms", "source": charter,
             "quote": "PART I Canadian Charter of Rights and Freedoms"},
            {"name": "part", "value": "PART I", "source": charter,
             "quote": "PART I Canadian Charter of Rights and Freedoms"},
            {"name": "part_of", "value": "CONSTITUTION ACT, 1982", "source": charter,
             "quote": "CONSTITUTION ACT, 1982 End note"},
            {"name": "enacted_as", "value": "Schedule B to the Canada Act 1982 , 1982, c. 11 (U.K.)",
             "source": index, "quote": "was enacted as Schedule B to the Canada Act 1982 , 1982, c. 11 (U.K.)"},
        ]}, "record__compose")[1]


def test_every_part_of_a_constitutional_citation_traces_to_the_official_pages():
    """The data comes from the fetched Justice Laws pages, copied as printed and
    quoted field by field; the plugin only arranges and styles it. Every field
    keeps the pages' origin -- correct, and honestly unverified (a page is not
    a database)."""
    h, session = make()
    charter, index = _justice_laws_pages(session)
    session.messages.append({"role": "user", "content": "i want charter to be cited"})
    composed = _compose_charter(h, session, charter, index)
    written = composed.content["written"]
    assert {name: entry["origin"] for name, entry in written.items()} == {
        "title": "extracted", "part": "extracted", "part_of": "extracted", "enacted_as": "extracted"}
    assert composed.content["missing"] == "" and composed.content["rejected"] == {}
    result = run_tool(h, session, "mcgill__cite", ref=composed.content["ref"])
    assert result.content["citation"] == (
        "*Canadian Charter of Rights and Freedoms*, Part I of the *Constitution Act, 1982*, "
        "being Schedule B to the *Canada Act 1982* (UK), 1982, c 11.")
    assert result.content["verified"] is False
    assert "pinpoint_added_by_you" not in result.content


def test_a_quote_still_has_to_be_in_the_page():
    """Spacing is forgiven; words are not."""
    import harness.core as harness_core
    h, session = make()
    charter, _ = _justice_laws_pages(session)
    composed = h._run_builtin(session, harness_core.BUILTIN_TOOLS[0], {
        "record_type": "constitutional",
        "fields": [{"name": "part_of", "value": "Constitution Act, 1982", "source": charter,
                    "quote": "being Part I of the Constitution Act, 1982"}]}, "record__compose")[1]
    assert composed.content["written"]["part_of"]["origin"] == "model"


def test_a_pinpoint_the_user_never_wrote_is_called_out():
    """Seen live: asked only to cite the Charter, the model passed s 2(b) and
    called it the user's. It is still shown (not refused), but the result
    tells the model and the card tells the user who added it."""
    h, session = make()
    charter, index = _justice_laws_pages(session)
    session.messages.append({"role": "user", "content": "i want charter to be cited"})
    composed = _compose_charter(h, session, charter, index)
    result = run_tool(h, session, "mcgill__cite", ref=composed.content["ref"], pinpoint="s 2(b)")
    assert "s 2(b)" in result.content["citation"] and result.content["verified"] is False
    assert "did not supply the pinpoint 's 2(b)'" in result.content["pinpoint_added_by_you"]
    rows = dict(result.blocks[0]["rows"])
    assert "added by the assistant" in rows["Pinpoint"]

    session.messages.append({"role": "user", "content": "cite s 7 of the Charter"})
    typed = run_tool(h, session, "mcgill__cite", ref=composed.content["ref"], pinpoint="s 7")
    assert "pinpoint_added_by_you" not in typed.content


def test_a_constitutional_citation_lands_in_the_legislation_section():
    h, session = make()
    from plugins import bibliography, mcgill
    source = "test:const"
    ref = session.records.put(Record("constitutional", {
        "title": Field("Constitution Act, 1867", "database", source_id=source),
        "jurisdiction": Field("UK", "database", source_id=source),
        "citation": Field("30 & 31 Vict, c 3", "database", source_id=source),
        "reprinted_in": Field("RSC 1985, Appendix II, No 5", "database", source_id=source)}, "test", "const"))
    citation = mcgill.cite(ctx_for(h, session, "mcgill"), mcgill.CiteParams(ref=ref))
    assert citation.content["verified"] is True
    result = bibliography.build(ctx_for(h, session, "bibliography"),
                                bibliography.BuildParams(refs=[citation.content["ref"]]))
    body = result.blocks[0]["body"]
    assert body.startswith("LEGISLATION")
    assert "Constitution Act, 1867 (UK), 30 & 31 Vict, c 3, reprinted in RSC 1985, Appendix II, No 5." in body
    assert result.content["unverified"] == 0
