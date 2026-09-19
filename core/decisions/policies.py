"""Conservative, per-question decision adoption policy.

Thresholds are deliberately configuration data, not a global magic number.
Until an application explicitly calibrates a question/risk/language slice, the
policy abstains and leaves the existing deterministic or human-review path in
control.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from .models import DecisionResult


@dataclass(frozen=True)
class DecisionPolicy:
    minimum_confidence: float
    allowed_languages: frozenset[str] = frozenset({"en"})

    def __post_init__(self):
        value = self.minimum_confidence
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("minimum_confidence must be in [0, 1]")

    def permits(self, result: DecisionResult, language: str) -> bool:
        return (
            result.confidence is not None
            and language.lower() in self.allowed_languages
            and result.confidence >= self.minimum_confidence
        )


class DecisionPolicyRegistry:
    """Look up explicit policies; unregistered questions always abstain."""

    def __init__(self, policies: Mapping[str, DecisionPolicy] | None = None):
        self._policies = dict(policies or {})

    def permits(self, result: DecisionResult, language: str) -> bool:
        policy = self._policies.get(result.question_id)
        return policy.permits(result, language) if policy else False
