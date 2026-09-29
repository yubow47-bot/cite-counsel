"""Behavior and provenance tests for the deterministic legal tools."""

import pytest

from core.legal_tools import court_days, limit_compute
from core.tool_contracts import Field, grounding_issues


def case_fields(record_id="canlii:case:alpha"):
    return {
        "style_of_cause": Field("Alpha v Beta", "database", source_id=record_id),
        "neutral_citation": Field("2020 SCC 1", "database", source_id=record_id),
    }


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
