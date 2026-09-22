"""Fail-closed HTTP adapter for TypeSafe Jev / OpenRouter Decisions.

The adapter only transports application-owned choice IDs. It intentionally has
no text-generation fallback: failures propagate as ``DecisionError`` so the
caller can abstain or keep the legacy path in control.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from core.spend_tracker import spend_tracker

from .client import DecisionError
from .models import ChoiceQuestion, DecisionResult, validate_result

logger = logging.getLogger(__name__)

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"


@dataclass(frozen=True)
class JevConfig:
    """Explicit transport configuration; no credentials are stored in code."""

    provider: str
    endpoint: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 8.0

    def __post_init__(self):
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise DecisionError("JEV timeout must be between zero and 120 seconds")

    @classmethod
    def from_environment(cls) -> "JevConfig":
        from llm_api.request_credentials import openrouter_key, typesafe_key
        provider = os.getenv("JEV_PROVIDER", "openrouter").strip().lower()
        timeout_seconds = float(os.getenv("JEV_TIMEOUT_SECONDS", "8"))
        if provider == "typesafe":
            return cls(
                provider=provider,
                endpoint=os.getenv("JEV_API_URL", TYPESAFE_SYSTEMONE_URL),
                model=os.getenv("JEV_MODEL", "jev-latest"),
                # A per-request key (request_credentials.use_credentials) wins
                # over the process-level TYPESAFE_API_KEY; see that module.
                api_key=typesafe_key(),
                timeout_seconds=timeout_seconds,
            )
        if provider == "openrouter":
            return cls(
                provider=provider,
                endpoint=os.getenv("JEV_API_URL", OPENROUTER_DECISIONS_URL),
                model=os.getenv("JEV_MODEL", "~typesafe/jev-latest"),
                api_key=openrouter_key(),
                timeout_seconds=timeout_seconds,
            )
        raise DecisionError("JEV_PROVIDER must be 'openrouter' or 'typesafe'")


class JevClient:
    """Invoke one closed-set Choice question and strictly validate its answer."""

    def __init__(self, config: JevConfig | None = None, session: requests.Session | None = None):
        self.config = config or JevConfig.from_environment()
        self._session = session or requests.Session()

    def close(self):
        self._session.close()

    def decide_choice(self, state: str | dict[str, Any] | list[Any], question: ChoiceQuestion) -> DecisionResult:
        if not self.config.api_key:
            raise DecisionError("JEV credentials are not configured")
        if spend_tracker.is_over_cap():
            raise DecisionError("Daily spend cap reached")

        started = time.perf_counter()
        try:
            response = self._session.post(
                self.config.endpoint,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.model,
                    "state": state,
                    "questions": {question.question_id: question.to_wire()},
                },
                timeout=self.config.timeout_seconds,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise DecisionError("JEV endpoint redirected the request")
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DecisionError("JEV request failed") from exc

        latency_ms = (time.perf_counter() - started) * 1000
        # Usage is billable independently of whether a malformed provider
        # response can be used. Record trustworthy token counts before schema
        # validation so an invalid response cannot make paid work disappear
        # from the local safety budget.
        response_model = payload.get("model") if isinstance(payload, dict) else self.config.model
        self._record_usage(payload, response_model if isinstance(response_model, str) else self.config.model)
        return self._parse_choice(payload, question, latency_ms)

    def _parse_choice(self, payload: Any, question: ChoiceQuestion, latency_ms: float) -> DecisionResult:
        if not isinstance(payload, dict):
            raise DecisionError("JEV response must be an object")
        model = payload.get("model")
        answers = payload.get("answers")
        if not isinstance(model, str) or not isinstance(answers, dict):
            raise DecisionError("JEV response is missing model or answers")
        answer = answers.get(question.question_id)
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise DecisionError("JEV response did not contain a Choice answer")

        selected_id = answer.get("choice")
        probabilities = answer.get("probabilities")
        confidence = answer.get("confidence")
        if not isinstance(selected_id, str) or selected_id not in question.criteria:
            raise DecisionError("JEV selected an option outside the requested closed set")
        if not isinstance(probabilities, dict) or set(probabilities) != set(question.criteria):
            raise DecisionError("JEV probabilities do not match the requested closed set")
        if any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in probabilities.values()):
            raise DecisionError("JEV probabilities must be numbers between zero and one")
        if abs(sum(probabilities.values()) - 1.0) > 0.001:
            raise DecisionError("JEV probabilities must sum to one")
        if confidence is not None and (not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
            raise DecisionError("JEV confidence must be between zero and one")

        return validate_result(DecisionResult(
            question_id=question.question_id,
            selected_id=selected_id,
            probabilities=probabilities,
            confidence=confidence,
            model=model,
            latency_ms=latency_ms,
        ), question)

    def _record_usage(self, payload: dict[str, Any], model: str) -> None:
        if not isinstance(payload, dict):
            raise DecisionError("JEV response must be an object")
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise DecisionError("JEV response missing token usage")
        input_tokens = usage.get("input_tokens", usage.get("inputTokens"))
        output_tokens = usage.get("output_tokens", usage.get("outputTokens"))
        if type(input_tokens) is not int or type(output_tokens) is not int:
            raise DecisionError("JEV response had invalid usage values")
        if input_tokens < 0 or output_tokens < 0:
            raise DecisionError("JEV response had negative usage values")
        try:
            spend_tracker.record_cost(self.config.provider, model or self.config.model, input_tokens, output_tokens)
        except Exception as exc:
            raise DecisionError("JEV spend tracking failed") from exc
