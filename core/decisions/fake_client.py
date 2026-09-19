"""Deterministic offline decision client for tests and fixture-driven evals."""

from __future__ import annotations

from typing import Any, Mapping

from .client import DecisionError
from .models import ChoiceQuestion, DecisionResult, validate_result


class FakeDecisionClient:
    """Return predeclared results keyed by stable question ID; never calls HTTP."""

    def __init__(self, responses: Mapping[str, DecisionResult] | None = None):
        self.responses = dict(responses or {})
        self.calls: list[tuple[Any, ChoiceQuestion]] = []

    def decide_choice(self, state: str | dict[str, Any] | list[Any], question: ChoiceQuestion) -> DecisionResult:
        self.calls.append((state, question))
        result = self.responses.get(question.question_id)
        if result is None:
            raise DecisionError(f"No fake response configured for {question.question_id!r}")
        if result.question_id != question.question_id:
            raise DecisionError("Fake response question ID does not match request")
        if result.selected_id not in question.criteria:
            raise DecisionError("Fake response selected an option outside the closed set")
        return validate_result(result, question)
