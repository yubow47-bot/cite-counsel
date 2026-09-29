"""Global daily spend cap tracker — thread-safe, in-memory.

Tracks cumulative USD spend across all paid model calls for the current UTC
day. When DAILY_SPEND_CAP_USD is reached, is_over_cap() returns True and
``openrouter_api.check_budget`` refuses further requests. The count resets on
process restart.

Prices are OpenRouter's, in USD per 1M tokens: a conservative over-estimate
that ignores cache and tier discounts.
"""

import logging
import os
import threading
import time


logger = logging.getLogger(__name__)

_PRICING: dict[str, dict[str, float]] = {
    "google/gemini-2.5-flash": {"input": 0.30, "output": 2.50},   # default vision model
    # OpenRouter (USD, 2026-09-08 pricing)
    "qwen/qwen3.7-flash":     {"input": 0.03, "output": 0.13},
    "openai/gpt-oss-20b":     {"input": 0.03, "output": 0.13},
    "z-ai/glm-4.7-flash":     {"input": 0.0605, "output": 0.40},
    "z-ai/glm-5.3-flash":     {"input": 0.15, "output": 0.50},  # OpenRouter, checked 2026-09-23
}

# Fallback for an unknown model: the highest USD/Mtokens rate in the table.
_FALLBACK_RATE = max(max(m["input"], m["output"]) for m in _PRICING.values())


def normalize_model_key(model: str) -> str:
    """Return the canonical pricing key: the model id, trimmed and lower-cased.

    Unknown ids keep the conservative fallback rate in ``_compute_cost``.
    """
    normalized = (model or "").strip().lower()
    return normalized

_DAILY_CAP = float(os.getenv("DAILY_SPEND_CAP_USD", "10"))


class SpendTracker:
    """Thread-safe in-memory global spend counter.

    Persistence was intentionally removed together with the Hugging Face
    integration: the daily cap now resets on process restart.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._utc_date: str = time.strftime("%Y-%m-%d", time.gmtime())
        self._total_spend: float = 0.0

    # ── public API ──

    def record_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
        """Record tokens for a paid LLM call and update the counter."""
        cost = self._compute_cost(provider, model, input_tokens, output_tokens)
        with self._lock:
            self._check_day_rollover()
            self._total_spend += cost

    def is_over_cap(self) -> bool:
        """Return True if cumulative spend has reached the daily cap."""
        with self._lock:
            self._check_day_rollover()
            return self._total_spend >= _DAILY_CAP

    @property
    def total_spend(self) -> float:
        with self._lock:
            self._check_day_rollover()
            return self._total_spend

    @property
    def remaining_budget(self) -> float:
        return max(0.0, _DAILY_CAP - self.total_spend)

    # ── internal ──

    def _compute_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        """Compute USD cost from token counts using the pricing table."""
        pricing_key = normalize_model_key(model)
        rates = _PRICING.get(pricing_key)
        if rates is None:
            logger.warning("Unknown model %s — using fallback rate $%.4f/1M", model, _FALLBACK_RATE)
            input_rate = output_rate = _FALLBACK_RATE
        else:
            input_rate = rates["input"]
            output_rate = rates["output"]

        return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

    def _check_day_rollover(self):
        """Reset spend if the UTC date has changed."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._utc_date != today:
            self._utc_date = today
            self._total_spend = 0.0


# Module-level singleton — import this from anywhere
spend_tracker = SpendTracker()
