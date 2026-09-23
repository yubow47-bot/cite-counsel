"""Behavior and provenance tests for the deterministic legal tools."""

import pytest

from core.legal_tools import (
    TOA_DRAFT_POLICY,
    cite_render,
    court_days,
    draft_table_of_authorities,
    limit_compute,
    quote_verify,
    table_of_authorities,
)
from core.tool_contracts import Artifact, Derivation, Field, grounding_issues, is_grounded


def case_fields(record_id="canlii:case:alpha"):
    return {
        "style_of_cause": Field("Alpha v Beta", "database", source_id=record_id),
        "neutral_citation": Field("2020 SCC 1", "database", source_id=record_id),
    }


def test_cite_render_uses_existing_renderer_and_keeps_exact_field_evidence(monkeypatch):
    fields = case_fields()
    calls = []

    def render(source_type, values):
        calls.append((source_type, values))
        return "Rendered by the established template."

    monkeypatch.setattr("core.mcgill_format.render_fields", render)
    artifact = cite_render("jurisprudence", fields)

    assert artifact.kind == "citation_text"
    assert artifact.content == "Rendered by the established template."
    assert calls == [("jurisprudence", {name: field.value for name, field in fields.items()})]
    assert artifact.derivation.inputs == tuple(fields.values())
    assert artifact.derivation.inputs[0] is fields["style_of_cause"]
    assert artifact.derivation.input_names == tuple(fields)
    assert is_grounded(artifact.derivation)


def test_cite_render_preserves_nested_computed_evidence():
    base = Field("2020 SCC 1", "database", source_id="canlii:case:alpha")
    derived = Field(
        "at para 7", "computed", rule_id="pinpoint.normalize.v1",
        derivation=Derivation((base,), "pinpoint.normalize.v1", ("citation",)),
    )
    artifact = cite_render("jurisprudence", {**case_fields(), "pinpoint": derived})

    assert artifact.derivation.inputs[-1] is derived
    assert is_grounded(artifact.derivation)


def test_table_of_authorities_composes_traceable_citations_and_nests_proofs():
    first = cite_render("jurisprudence", case_fields("canlii:case:one"))
    second = cite_render("jurisprudence", {
        "style_of_cause": Field("Gamma v Delta", "database", source_id="canlii:case:two"),
        "neutral_citation": Field("2021 SCC 2", "database", source_id="canlii:case:two"),
    })

    toa = table_of_authorities([first, second])

    assert toa.kind == "table_of_authorities"
    assert toa.content == "TABLE OF AUTHORITIES\n\n1. *Alpha v Beta*, 2020 SCC 1.\n2. *Gamma v Delta*, 2021 SCC 2."
    assert toa.derivation.inputs == (first.derivation, second.derivation)
    assert toa.derivation.input_names == ("citation_1", "citation_2")
    assert is_grounded(toa.derivation)


def test_table_of_authorities_rejects_untraceable_citations_by_default():
    untraceable = cite_render("jurisprudence", {
        "style_of_cause": Field("Alpha v Beta", "user"),
        "neutral_citation": Field("2020 SCC 1", "database", source_id="canlii:case:one"),
    })

    with pytest.raises(ValueError, match="unsupported entries: 1"):
        table_of_authorities([untraceable])


def test_draft_toa_is_explicitly_labelled_and_retains_unverified_evidence():
    citation = cite_render("jurisprudence", {
        "style_of_cause": Field("Alpha v Beta", "user"),
        "neutral_citation": Field("2020 SCC 1", "database", source_id="canlii:case:one"),
    })

    toa = table_of_authorities([citation], policy=TOA_DRAFT_POLICY)
    wrapper = draft_table_of_authorities([citation])

    assert toa.kind == "table_of_authorities_draft"
    assert toa.content.startswith("DRAFT TABLE OF AUTHORITIES — UNVERIFIED; NOT COURT-FORMAT COMPLIANT")
    assert wrapper == toa
    assert [(issue.path, issue.reason) for issue in grounding_issues(toa.derivation)] == [
        (("citation_1", "style_of_cause"), "unverified_user")
    ]


@pytest.mark.parametrize("coverage", ["partial", "complete"])
def test_quote_verify_confirms_only_exact_verbatim_substrings(coverage):
    quote = Field("The Court allows the appeal.", "user")
    source = Field("Reasons: The Court allows the appeal. Costs follow.", "database", source_id="canlii:case:1")

    finding = quote_verify(quote, source, coverage=coverage)

    assert finding.verdict == "confirmed"
    assert finding.coverage == coverage
    assert finding.derivation.inputs == (quote, source)
    assert finding.derivation.input_names == ("quote", "source")


@pytest.mark.parametrize("quoted_text", [
    "The court allows the appeal.",
    "The Court  allows the appeal.",
])
def test_quote_verify_is_case_and_whitespace_sensitive(quoted_text):
    quote = Field(quoted_text, "user")
    source = Field("The Court allows the appeal.", "database", source_id="canlii:case:1")

    assert quote_verify(quote, source, coverage="complete").verdict == "contradicted"


def test_quote_verify_missing_quote_with_partial_source_is_inconclusive():
    finding = quote_verify(
        Field("A sentence outside this excerpt.", "user"),
        Field("Only the first page is available.", "database", source_id="canlii:case:1"),
    )

    assert finding.verdict == "inconclusive"
    assert finding.coverage == "partial"
    assert "partial source coverage" in finding.detail


def test_quote_verify_missing_quote_with_complete_source_is_contradicted():
    finding = quote_verify(
        Field("A sentence not in the reasons.", "user"),
        Field("The complete reasons are supplied here.", "database", source_id="canlii:case:1"),
        coverage="complete",
    )

    assert finding.verdict == "contradicted"
    assert finding.coverage == "complete"


def test_table_of_authorities_rejects_non_citation_artifacts():
    other = Artifact("deadline_date", "2026-01-01", Derivation((Field("x", "user"),), "limit.compute.v1"))
    with pytest.raises(ValueError, match="only text citation artifacts"):
        table_of_authorities([other])


def date_field(value):
    return Field(value, "database", source_id="court-calendar:example:1")


def rule_field(value="Declared calendar rule"):
    return Field(value, "user")


def test_limit_compute_adds_calendar_days_and_keeps_every_field_input():
    start = date_field("2024-02-27")
    days = Field("2", "user")
    rule = rule_field("calendar days; exclude start; include end")
    holiday = date_field("2024-02-29")

    result = limit_compute(
        start, days, rule, holidays=(holiday,), mode="calendar_days", weekend_days=(5, 6)
    )

    assert result.kind == "deadline_date"
    assert result.content == "2024-02-29"  # Leap day remains an ordinary calendar day.
    assert result.derivation.inputs == (start, days, rule, holiday)
    assert result.derivation.input_names == ("start", "days", "rule", "holiday_1")
    assert [(issue.path, issue.reason) for issue in grounding_issues(result.derivation)] == [
        (("days",), "unverified_user"),
        (("rule",), "unverified_user"),
    ]


def test_limit_compute_rolls_calendar_result_past_weekend_and_holiday():
    result = limit_compute(
        date_field("2024-05-24"),  # Friday
        Field("1", "user"),
        rule_field(),
        roll_forward=True,
        holidays=(date_field("2024-05-27"),),  # Monday
        mode="calendar_days",
        weekend_days=(5, 6),
    )

    assert result.content == "2024-05-28"


def test_limit_compute_court_day_mode_counts_only_eligible_days():
    result = limit_compute(
        date_field("2024-05-24"),  # Friday
        Field("2", "user"),
        rule_field(),
        holidays=(date_field("2024-05-27"),),
        mode="court_days",
        weekend_days=(5, 6),
    )

    assert result.content == "2024-05-29"


def test_court_days_honours_explicit_endpoints_weekends_and_holidays():
    start = date_field("2024-05-24")  # Friday
    end = date_field("2024-05-31")  # Friday
    rule = rule_field("Exclude start; include end; weekends Saturday/Sunday")
    holiday = date_field("2024-05-27")

    result = court_days(
        start, end, rule, (holiday,), weekend_days=(5, 6), include_start=False, include_end=True
    )

    assert result.kind == "court_days"
    assert result.content == "4"  # Tue–Fri; Monday is caller-supplied holiday.
    assert result.derivation.inputs == (start, end, rule, holiday)
    assert result.derivation.input_names == ("start", "end", "calendar_rule", "holiday_1")


@pytest.mark.parametrize(
    "call",
    [
        lambda: limit_compute(date_field("2024-02-30"), Field("1", "user"), rule_field(), mode="calendar_days", weekend_days=(5, 6)),
        lambda: limit_compute(date_field("2024-01-01"), Field("-1", "user"), rule_field(), mode="calendar_days", weekend_days=(5, 6)),
        lambda: limit_compute(date_field("2024-01-01"), Field("1", "user"), rule_field(), mode="court_days", weekend_days=(5, 6), roll_forward=True),
        lambda: court_days(date_field("2024-01-02"), date_field("2024-01-01"), rule_field(), weekend_days=(5, 6)),
        lambda: court_days(date_field("2024-01-01"), date_field("2024-01-02"), rule_field(), weekend_days=(5, 5)),
    ],
)
def test_date_tools_reject_invalid_dates_counts_or_calendar_configuration(call):
    with pytest.raises(ValueError):
        call()


def test_limit_compute_rejects_calendar_overflow():
    with pytest.raises(ValueError, match="exceeds the supported calendar range"):
        limit_compute(
            date_field("9999-12-31"), Field("1", "user"), rule_field(),
            mode="calendar_days", weekend_days=(5, 6),
        )
