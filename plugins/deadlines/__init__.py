"""Mechanical date arithmetic for deadlines, with every input shown back.

The plugin knows no court calendar and no limitation rule: the user supplies
the start date, the number of days, which weekdays are closed and the
holidays. The result card lists all of them so a wrong input is visible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field as PField

from harness.plugin import Plugin, Result, Tool


class DeadlineParams(BaseModel):
    start: str = PField(description="Start date, YYYY-MM-DD")
    days: int = PField(ge=0, le=36600)
    mode: Literal["calendar_days", "court_days"] = PField(
        description="calendar_days counts every date; court_days skips weekends and holidays")
    rule: str = PField(description="The rule the user is applying, in their words (kept as a label only)")
    weekend_days: list[int] = PField(default=[5, 6], description="Closed weekdays, Monday=0 … Sunday=6")
    holidays: list[str] = PField(default_factory=list, description="Closed dates the user listed, YYYY-MM-DD")
    roll_forward: bool = PField(default=False, description="calendar_days only: move a closed end date forward")


def compute(ctx, p: DeadlineParams) -> Result:
    from core.legal_tools import limit_compute
    from core.tool_contracts import Field
    start, _ = ctx.provenance(p.start)
    days, _ = ctx.provenance(str(p.days))
    rule, _ = ctx.provenance(p.rule) if p.rule else (Field("unspecified", "model"), "")
    holidays = tuple(ctx.provenance(h)[0] for h in p.holidays)
    artifact = limit_compute(start, days, rule,
                             p.roll_forward, holidays,
                             mode=p.mode, weekend_days=tuple(p.weekend_days))
    ref = ctx.save(artifact)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rows = [["Result date", artifact.content], ["Start date (not counted)", p.start], ["Days", str(p.days)],
            ["Counting method", "Calendar days" if p.mode == "calendar_days" else "Court days"],
            ["Weekend days", ", ".join(names[d] for d in sorted(p.weekend_days)) or "None"],
            ["Holidays", ", ".join(p.holidays) or "None"], ["Rule (as you gave it)", p.rule or "Not specified"]]
    if p.mode == "calendar_days":
        rows.insert(4, ["Rolls forward past a closed day", "Yes" if p.roll_forward else "No"])
    block = {"type": "card", "title": "Deadline calculation", "rows": rows,
             "note": "This only does the date arithmetic on the inputs listed above -- it does not judge "
                     "whether the rule applies, and does not know any court holiday not listed here. "
                     "Check each row."}
    inputs = [{"name": name, "value": field.value, "origin": field.origin}
              for name, field in (("start", start), ("days", days), ("rule", rule))]
    inputs += [{"name": "holiday", "value": field.value, "origin": field.origin} for field in holidays]
    inputs += [{"name": name, "value": value,
                "origin": "default" if name not in p.model_fields_set else ctx.provenance(str(value))[0].origin}
               for name, value in (("mode", p.mode), ("weekend_days", p.weekend_days),
                                   ("roll_forward", p.roll_forward))]
    rows.append(["Input origins", "; ".join(f"{item['name']}={item['origin']}" for item in inputs)])
    return Result({"ref": ref, "date": artifact.content, "inputs": inputs}, [block], final=True)


PLUGIN = Plugin(
    name="deadlines",
    title="Deadline calculator",
    description="Compute calendar or court days from given inputs and explicitly reported defaults.",
    tools=[Tool("compute", "Compute a deadline date from explicit inputs.", DeadlineParams, compute)],
    default_enabled=False,
)
