"""Typed, fail-closed decision clients used by optional decision providers."""

from .client import DecisionClient, DecisionError
from .fake_client import FakeDecisionClient
from .models import ChoiceQuestion, DecisionResult

__all__ = [
    "ChoiceQuestion",
    "DecisionClient",
    "DecisionError",
    "DecisionResult",
    "FakeDecisionClient",
]
