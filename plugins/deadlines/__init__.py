"""Mechanical date arithmetic for deadlines, with every input shown back.

The plugin knows no court calendar and no limitation rule: the user supplies
the start date, the number of days, which weekdays are closed and the
holidays. The result card lists all of them so a wrong input is visible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field as PField

from core.grounding import grounded_reply_guard
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
    artifact = limit_compute(Field(p.start, "user"), Field(str(p.days), "user"), Field(p.rule or "unspecified", "user"),
                             p.roll_forward, tuple(Field(h, "user") for h in p.holidays),
                             mode=p.mode, weekend_days=tuple(p.weekend_days))
    ref = ctx.save(artifact)
    names = "一二三四五六日"
    rows = [["结果日期", artifact.content], ["起算日（不计入）", p.start], ["天数", str(p.days)],
            ["计算方式", "日历日" if p.mode == "calendar_days" else "工作日"],
            ["休息日", "、".join("周" + names[d] for d in sorted(p.weekend_days)) or "无"],
            ["节假日", "、".join(p.holidays) or "无"], ["依据（你提供）", p.rule or "未说明"]]
    if p.mode == "calendar_days":
        rows.insert(4, ["遇休息日顺延", "是" if p.roll_forward else "否"])
    block = {"type": "card", "title": "期限计算", "rows": rows,
             "note": "只按上面列出的输入做日期运算，不判断规则是否适用，也不包含未列出的法院休息日。请逐项核对。"}
    return Result({"ref": ref, "date": artifact.content}, [block], final=True)


PLUGIN = Plugin(
    name="deadlines",
    title="期限计算",
    description="Add a number of calendar or court days to a date, using weekends and holidays the user supplies.",
    instructions="Ask the user for any input they did not give (start date, number of days, counting method, "
                 "holidays). Never assume a court's holidays.",
    tools=[Tool("compute", "Compute a deadline date from explicit inputs.", DeadlineParams, compute)],
    default_enabled=False,
    reply_guard=grounded_reply_guard,
)
