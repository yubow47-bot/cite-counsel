"""Deterministic legal-tool functions built on the evidence contracts.

The module deliberately contains no source lookup, legal reasoning, court-rule
selection, or model calls.  Its public functions are the callables that the
tool registry can expose under ``cite.render``, ``doc.table_of_authorities``,
and ``quote.verify``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Literal

from core import mcgill_format
from core.tool_contracts import Artifact, Derivation, Field, Finding, is_grounded


CITATION_ARTIFACT_KIND = "citation_text"
TOA_STRICT_POLICY = "traceable_only"
TOA_DRAFT_POLICY = "allow_unverified_draft"
ToAPolicy = Literal["traceable_only", "allow_unverified_draft"]
DateMode = Literal["calendar_days", "court_days"]


def _field_mapping(fields: Mapping[str, Field]) -> dict[str, Field]:
    """Copy and validate names without changing any field provenance."""
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("Citation fields must be a nonempty mapping of names to Field objects")
    copied = dict(fields)
    if any(not isinstance(name, str) or not name.strip() or not isinstance(field, Field)
           for name, field in copied.items()):
        raise ValueError("Citation fields must be a nonempty mapping of names to Field objects")
    return copied


def cite_render(source_type: str, fields: Mapping[str, Field]) -> Artifact:
    """Render a citation without discarding the provenance of its fields.

    ``mcgill_format.render_fields`` remains the single source of the McGill
    templates.  The exact input ``Field`` instances become the named leaves of
    the artifact's derivation, retaining source IDs, spans, and any nested
    computed-field derivations.
    """
    if not isinstance(source_type, str) or not source_type.strip():
        raise ValueError("Citation source type must be nonempty text")
    evidence = _field_mapping(fields)
    content = mcgill_format.render_fields(
        source_type, {name: field.value for name, field in evidence.items()}
    )
    return Artifact(
        kind=CITATION_ARTIFACT_KIND,
        content=content,
        derivation=Derivation(
            tuple(evidence.values()), "cite.render.v1", tuple(evidence.keys())
        ),
    )


def _citation_artifacts(artifacts: list[Artifact]) -> tuple[Artifact, ...]:
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("A table of authorities requires a nonempty list of citation artifacts")
    result = tuple(artifacts)
    if any(not isinstance(artifact, Artifact) or artifact.kind != CITATION_ARTIFACT_KIND
           or not isinstance(artifact.content, str) for artifact in result):
        raise ValueError("A table of authorities accepts only text citation artifacts")
    return result


def table_of_authorities(
    artifacts: list[Artifact], *, policy: ToAPolicy = TOA_STRICT_POLICY
) -> Artifact:
    """Compose supplied citations into a deterministic, generic authority list.

    The normal policy accepts only citations whose complete derivation is
    traceable according to ``is_grounded``.  ``allow_unverified_draft`` is an
    explicit opt-in for working drafts.  Its content is prominently labelled
    as unverified and never represents court-format compliance.  This tool
    preserves input order because Artifact carries neither jurisdiction nor
    authority category, both of which are needed for court-specific ordering.
    """
    citations = _citation_artifacts(artifacts)
    if policy not in {TOA_STRICT_POLICY, TOA_DRAFT_POLICY}:
        raise ValueError("Unknown table-of-authorities policy")

    untraceable = [index for index, citation in enumerate(citations, start=1)
                   if not is_grounded(citation.derivation)]
    if untraceable and policy == TOA_STRICT_POLICY:
        joined = ", ".join(str(index) for index in untraceable)
        raise ValueError(f"Citation artifacts must be traceable; unsupported entries: {joined}")

    lines: list[str] = []
    kind = "table_of_authorities"
    rule_id = "doc.table_of_authorities.v1"
    if policy == TOA_DRAFT_POLICY:
        kind = "table_of_authorities_draft"
        rule_id = "doc.table_of_authorities.draft.v1"
        lines.append("DRAFT TABLE OF AUTHORITIES — UNVERIFIED; NOT COURT-FORMAT COMPLIANT")
    else:
        lines.append("TABLE OF AUTHORITIES")
    lines.append("")
    lines.extend(f"{index}. {citation.content}" for index, citation in enumerate(citations, start=1))

    return Artifact(
        kind=kind,
        content="\n".join(lines),
        derivation=Derivation(
            tuple(citation.derivation for citation in citations),
            rule_id,
            tuple(f"citation_{index}" for index in range(1, len(citations) + 1)),
        ),
    )


def draft_table_of_authorities(artifacts: list[Artifact]) -> Artifact:
    """Build an explicitly unverified draft under the opt-in draft policy."""
    return table_of_authorities(artifacts, policy=TOA_DRAFT_POLICY)


def quote_verify(
    quote: Field, source: Field, coverage: Literal["complete", "partial"] = "partial"
) -> Finding:
    """Check an exact, case- and whitespace-sensitive quote against supplied text.

    Partial coverage can confirm text that is present, but an absent quote is
    inconclusive because unexamined portions of the original may contain it.
    Complete coverage turns an absence into a contradiction.  The result says
    only what was found in the supplied source Field; provenance is retained in
    the Finding derivation for callers to assess independently.
    """
    if not isinstance(quote, Field) or not isinstance(source, Field):
        raise ValueError("Quote verification requires quote and source Field objects")
    if not quote.value:
        raise ValueError("Quote verification requires nonempty quoted text")
    if not source.value:
        raise ValueError("Quote verification requires nonempty source text")
    if coverage not in {"complete", "partial"}:
        raise ValueError("Quote verification coverage must be complete or partial")

    if quote.value in source.value:
        verdict = "confirmed"
        detail = "The quote appears verbatim in the supplied source text."
    elif coverage == "partial":
        verdict = "inconclusive"
        detail = "The quote is absent from the supplied source text, but only partial source coverage was checked."
    else:
        verdict = "contradicted"
        detail = "The quote does not appear verbatim in the supplied complete source text."

    return Finding(
        verdict=verdict,
        detail=detail,
        coverage=coverage,
        derivation=Derivation((quote, source), "quote.verify.exact.v1", ("quote", "source")),
    )


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


# Friendly Python aliases; the registry maps the canonical names above to
# dotted tool names rather than making this module mimic a package hierarchy.
render_citation = cite_render
verify_quote = quote_verify


__all__ = [
    "CITATION_ARTIFACT_KIND",
    "TOA_DRAFT_POLICY",
    "TOA_STRICT_POLICY",
    "cite_render",
    "court_days",
    "draft_table_of_authorities",
    "limit_compute",
    "quote_verify",
    "render_citation",
    "table_of_authorities",
    "verify_quote",
]
