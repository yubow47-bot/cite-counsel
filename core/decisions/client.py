"""Decision-client interface kept independent from text-generation clients."""

from __future__ import annotations

from typing import Any, Protocol

from .models import ChoiceQuestion, DecisionResult


class DecisionError(RuntimeError):
    """A provider, timeout, or schema failure that must not be silently hidden."""


class DecisionClient(Protocol):
    """Answers closed-set questions without returning free-form factual text."""

    def decide_choice(self, state: str | dict[str, Any] | list[Any], question: ChoiceQuestion) -> DecisionResult:
        ...
