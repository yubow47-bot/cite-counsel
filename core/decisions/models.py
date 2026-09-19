"""Provider-neutral data contracts for constrained application decisions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class ChoiceQuestion:
    """A closed-set decision whose option IDs are owned by application code."""

    question_id: str
    instructions: str
    criteria: Mapping[str, str | None]

    def __post_init__(self) -> None:
        if not isinstance(self.question_id, str) or not self.question_id.strip():
            raise ValueError("question_id is required")
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("instructions is required")
        if not isinstance(self.criteria, Mapping) or not 2 <= len(self.criteria) <= 255:
            raise ValueError("Choice questions require 2 to 255 options")
        if any(not isinstance(option_id, str) or not option_id.strip() for option_id in self.criteria):
            raise ValueError("Choice option IDs must be non-empty")
        if any(value is not None and not isinstance(value, str) for value in self.criteria.values()):
            raise ValueError("Choice criteria must be text or null")
        object.__setattr__(self, "criteria", MappingProxyType(dict(self.criteria)))

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True)
class DecisionResult:
    """A validated answer to one application-owned decision question."""

    question_id: str
    selected_id: str
    probabilities: Mapping[str, float]
    confidence: float | None
    model: str
    latency_ms: float


def validate_result(result: DecisionResult, question: ChoiceQuestion) -> DecisionResult:
    from .client import DecisionError
    if result.question_id != question.question_id or result.selected_id not in question.criteria:
        raise DecisionError("Decision selected an option outside the requested question")
    if not isinstance(result.probabilities, Mapping) or set(result.probabilities) != set(question.criteria):
        raise DecisionError("Decision probability keys do not match options")
    values = result.probabilities.values()
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise DecisionError("Decision probabilities must be finite numbers in [0, 1]")
    if abs(sum(values) - 1) > 0.001 or result.probabilities[result.selected_id] < max(values):
        raise DecisionError("Decision distribution or selected option is inconsistent")
    c = result.confidence
    if c is not None and (type(c) not in (int, float) or not math.isfinite(c) or not 0 <= c <= 1):
        raise DecisionError("Decision confidence must be finite and in [0, 1]")
    if not isinstance(result.model, str) or not result.model.strip():
        raise DecisionError("Decision model is missing")
    return result
