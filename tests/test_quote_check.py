"""A quotation is looked up in the judgment's own text; its paragraph becomes the pinpoint."""
import pytest

from core import mcgill_format, quote_check

TEXT = quote_check.normalize(
    "R. v. Sharma\nCollection\nSupreme Court Judgments\n[2022] 3 S.C.R. 147\n"
    "I. Introduction\n[1] Conditional sentences are a form of punishment that allow offenders to serve "
    "their sentences in the community, rather than in jail.\n"
    "[2] Parliament created the conditional sentencing regime in 1996.\n"
    "[3] In 2012, it amended the regime to make conditional sentences unavailable for certain serious "
    "offences.\n[4] Two examples illuminate Abella J.’s reasoning — and the burden­ of proof.\n")
URL = "https://decisions.scc-csc.ca/x"


@pytest.fixture(autouse=True)
def offline_judgment(monkeypatch):
    monkeypatch.setattr(quote_check, "judgment",
                        lambda citation: {"text": TEXT, "url": URL, "citations": ["2022 SCC 39"]}
                        if citation == "2022 SCC 39" else None)


def sharma(**extra):
    """A case item as a future record store would hand it to the quote-check tool."""
    fields = {"style_of_cause": {"value": "R v Sharma", "origin": "database", "source_id": URL},
              "neutral_citation": {"value": "2022 SCC 39", "origin": "database", "source_id": URL}}
    fields.update(extra)
    return {"source_type": "jurisprudence", "base": None, "fields": fields}


def test_exact_quote_gets_its_paragraph():
    result = quote_check.check(sharma(), "“Parliament created the conditional sentencing regime in 1996.”")
    assert result["verdict"] == "exact" and result["pinpoint"] == "at para 2"
    start, end = result["match"]
    assert result["excerpt"][start:end] == "Parliament created the conditional sentencing regime in 1996."


def test_typography_does_not_matter_but_words_do():
    located = quote_check.locate(TEXT, "Abella J.'s reasoning - and the burden of proof")
    assert located.verdict == "exact" and located.paragraphs == (4,)
    assert quote_check.locate(TEXT, "Abella J.'s reasoning and the burden of proof").verdict == "not_found"


def test_capitalization_is_reported_not_forgiven():
    assert quote_check.locate(TEXT, "parliament created the conditional").verdict == "case_differs"


def test_ellipsis_spans_paragraphs():
    located = quote_check.locate(TEXT, "Parliament created the conditional sentencing regime ... it amended the regime")
    assert located.verdict == "exact" and quote_check.pinpoint_for(located.paragraphs) == "at paras 2–3"


def test_a_citation_line_in_the_header_is_not_a_paragraph():
    assert quote_check.locate(TEXT, "Supreme Court Judgments").paragraphs == ()


def test_finding_is_traced_to_the_database_text():
    result = quote_check.check(sharma(), "Parliament created the conditional sentencing regime in 1996.")
    assert result["finding"].verdict == "confirmed" and result["finding"].coverage == "complete"
    source = result["finding"].derivation.inputs[1]
    assert source.origin == "database" and source.source_id == URL


def test_verdicts_distinguish_absent_from_differently_capitalized():
    absent = quote_check.check(sharma(), "The Charter guarantees a right to a jury trial.")
    assert absent["verdict"] == "not_found" and not absent["pinpoint"]
    differs = quote_check.check(sharma(), "parliament created the conditional")
    assert differs["verdict"] == "case_differs" and differs["finding"].verdict == "inconclusive"


def test_user_typed_citations_are_not_looked_up(monkeypatch):
    """Only a citation the database supplied names the judgment to search."""
    called = []
    monkeypatch.setattr(quote_check, "judgment", lambda citation: called.append(citation) or None)
    item = sharma(style_of_cause={"value": "R v Sharma", "origin": "user"},
                 neutral_citation={"value": "2022 SCC 39", "origin": "user"})
    with pytest.raises(ValueError):
        quote_check.check(item, "Parliament created the conditional sentencing regime")
    assert called == []


def test_only_database_case_citations_name_a_judgment():
    assert quote_check.case_citation(sharma()) == "2022 SCC 39"
    other = {"source_type": "jurisprudence", "base": "*Some Case*, [1930] AC 124.", "fields": {}}
    assert quote_check.case_citation(other) == ""


def test_pinpoint_follows_the_first_citation_not_the_parallel_one():
    fields = {"style_of_cause": "R v Sharma", "neutral_citation": "2022 SCC 39",
              "reporter": "[2022] 3 SCR 147", "pinpoint": "at para 1"}
    assert mcgill_format.render_fields("jurisprudence", fields) == "*R v Sharma*, 2022 SCC 39 at para 1, [2022] 3 SCR 147."
    old = {"style_of_cause": "Edwards v Canada (AG)", "reporter": "[1930] AC 124", "court": "PC", "pinpoint": "at 128"}
    assert mcgill_format.render_fields("jurisprudence", old) == "*Edwards v Canada (AG)*, [1930] AC 124 at 128 (PC)."
