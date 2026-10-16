"""Ephemeral turn budget. The harness owns execution; plugins supply work."""
from dataclasses import dataclass, field
import time


@dataclass
class TurnBudget:
    deadline: float
    answer_reserve: float
    max_calls: int
    receipt_start: int
    tool_count: int = 0
    review_attempted: bool = False
    phase: str = "investigation"
    reviewed_call_id: str | None = None
    review_audit: dict = field(default_factory=dict)
    candidate_reply: str = ""

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def can_call(self) -> bool:
        reserve = self.answer_reserve if self.phase == "investigation" else 3
        return self.tool_count < self.max_calls and self.remaining() > reserve

    def review_deadline(self) -> float | None:
        if self.review_attempted or self.remaining() < 20:
            return None
        return min(time.monotonic() + 40, self.deadline - 8)
