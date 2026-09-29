"""Deterministic date arithmetic for the deadlines plugin, built on the evidence contracts.

The module deliberately contains no source lookup, legal reasoning, court-rule
selection, or model calls.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from core.tool_contracts import Artifact, Derivation, Field


DateMode = Literal["calendar_days", "court_days"]


def _nonempty_field(field: Field, name: str) -> Field:
    if not isinstance(field, Field) or not field.value:
        raise ValueError(f"{name} must be a nonempty Field")
    return field


def _parse_iso_date(field: Field, name: str) -> date:
    _nonempty_field(field, name)
    try:
        parsed = date.fromisoformat(field.value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must use an ISO calendar date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != field.value:
        raise ValueError(f"{name} must use an ISO calendar date (YYYY-MM-DD)")
    return parsed


def _parse_days(field: Field) -> int:
    _nonempty_field(field, "days")
    if not field.value.isascii() or not field.value.isdecimal():
        raise ValueError("days must be a nonnegative base-10 integer Field")
    if len(field.value) > 5 or int(field.value) > 36600:
        raise ValueError("A single date calculation is limited to 36600 days")
    return int(field.value)


def _calendar_inputs(
    holidays: tuple[Field, ...], weekend_days: tuple[int, ...]
) -> tuple[tuple[Field, ...], frozenset[date], frozenset[int]]:
    if not isinstance(holidays, tuple) or any(not isinstance(field, Field) for field in holidays):
        raise ValueError("holidays must be a tuple of Field objects")
    holiday_dates = frozenset(_parse_iso_date(field, "holiday") for field in holidays)
    if (not isinstance(weekend_days, tuple)
            or any(type(day) is not int or day < 0 or day > 6 for day in weekend_days)
            or len(set(weekend_days)) != len(weekend_days)):
        raise ValueError("weekend_days must be a tuple of unique weekday numbers from 0 through 6")
    if len(weekend_days) == 7:
        raise ValueError("A calendar must have at least one eligible weekday")
    return holidays, holiday_dates, frozenset(weekend_days)


def _next_date(value: date) -> date:
    try:
        return value + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError("Date computation exceeds the supported calendar range") from exc


def _add_calendar_days(value: date, days: int) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError as exc:
        raise ValueError("Date computation exceeds the supported calendar range") from exc


def _is_court_day(value: date, holidays: frozenset[date], weekend_days: frozenset[int]) -> bool:
    return value.weekday() not in weekend_days and value not in holidays


def _roll_forward(value: date, holidays: frozenset[date], weekend_days: frozenset[int]) -> date:
    while not _is_court_day(value, holidays, weekend_days):
        value = _next_date(value)
    return value


def limit_compute(
    start: Field,
    days: Field,
    rule: Field,
    roll_forward: bool = False,
    holidays: tuple[Field, ...] = (),
    *,
    mode: DateMode,
    weekend_days: tuple[int, ...],
) -> Artifact:
    """Calculate a date under caller-supplied calendar parameters.

    ``rule`` is retained as evidence only and is never parsed.  Callers must
    choose ``mode`` and explicitly supply the weekday numbers treated as
    weekends (Monday is 0; Sunday is 6).  In ``calendar_days`` mode the
    algorithm excludes ``start`` and adds ``days`` ordinary calendar dates;
    an optional roll-forward then moves a weekend/holiday result to the next
    eligible date.  In ``court_days`` mode it counts that many eligible dates
    strictly after ``start``; roll-forward is consequently invalid.

    This does not determine a limitation period, a court's calendar, or the
    applicability of the supplied rule.  It only executes these declared
    calendar inputs and preserves all Field evidence in the derivation.
    """
    start_date = _parse_iso_date(start, "start")
    day_count = _parse_days(days)
    _nonempty_field(rule, "rule")
    if type(roll_forward) is not bool:
        raise ValueError("roll_forward must be a boolean")
    if mode not in {"calendar_days", "court_days"}:
        raise ValueError("mode must be calendar_days or court_days")
    holiday_fields, holiday_dates, weekend = _calendar_inputs(holidays, weekend_days)
    if mode == "court_days" and roll_forward:
        raise ValueError("roll_forward is only defined for calendar_days mode")

    if mode == "calendar_days":
        result = _add_calendar_days(start_date, day_count)
        if roll_forward:
            result = _roll_forward(result, holiday_dates, weekend)
    else:
        result = start_date
        for _ in range(day_count):
            result = _next_date(result)
            while not _is_court_day(result, holiday_dates, weekend):
                result = _next_date(result)

    inputs = (start, days, rule, *holiday_fields)
    names = ("start", "days", "rule", *(f"holiday_{index}" for index in range(1, len(holiday_fields) + 1)))
    return Artifact(
        kind="deadline_date",
        content=result.isoformat(),
        derivation=Derivation(inputs, f"limit.compute.{mode}.v1;weekend={','.join(map(str, sorted(weekend)))};roll={int(roll_forward)}", names),
    )


def court_days(
    start: Field,
    end: Field,
    calendar_rule: Field,
    holidays: tuple[Field, ...] = (),
    *,
    weekend_days: tuple[int, ...],
    include_start: bool = False,
    include_end: bool = True,
) -> Artifact:
    """Count eligible calendar dates in an explicitly bounded interval.

    ``calendar_rule`` is evidence supplied by the caller; it is not natural-
    language configuration and is never interpreted.  The explicit boundary
    flags select whether each endpoint is counted.  Eligible dates are those
    not present in ``holidays`` and whose ``weekday()`` is absent from the
    caller-supplied ``weekend_days`` tuple.  This is a mechanical count, not a
    statement about any court's actual operating calendar or filing rule.
    """
    start_date = _parse_iso_date(start, "start")
    end_date = _parse_iso_date(end, "end")
    _nonempty_field(calendar_rule, "calendar_rule")
    if type(include_start) is not bool or type(include_end) is not bool:
        raise ValueError("include_start and include_end must be booleans")
    if end_date < start_date:
        raise ValueError("end must be on or after start")
    if (end_date - start_date).days > 36600:
        raise ValueError("A single calendar interval is limited to 36600 days")
    holiday_fields, holiday_dates, weekend = _calendar_inputs(holidays, weekend_days)

    count = 0
    current = start_date
    while current <= end_date:
        included = (current != start_date or include_start) and (current != end_date or include_end)
        if included and _is_court_day(current, holiday_dates, weekend):
            count += 1
        if current == end_date:
            break
        current = _next_date(current)

    inputs = (start, end, calendar_rule, *holiday_fields)
    names = ("start", "end", "calendar_rule", *(f"holiday_{index}" for index in range(1, len(holiday_fields) + 1)))
    return Artifact(
        kind="court_days",
        content=str(count),
        derivation=Derivation(inputs, f"date.court_days.v1;weekend={','.join(map(str, sorted(weekend)))};start={int(include_start)};end={int(include_end)}", names),
    )

